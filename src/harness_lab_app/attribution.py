"""Structured harness attribution from a run's opencode logs.

Answers "which of the engineering harness did the agent actually exercise?" —
machine-readable, aggregable across trials, complementing the qualitative
``bench-audit``. It parses the per-step opencode logs in ``data/runs/<id>/steps``
and extracts observable signals:

- tool usage (read / edit / bash / shell) and their counts,
- files read and edited,
- which governance docs and memory the harness provides that the agent actually
  read (coverage) — the strongest "harness applied" signal,
- bash command histogram and agent modes.

Honest limitation: opencode injects skills into the model context rather than
reading each ``SKILL.md`` as a file, so skill *invocation* is only weakly
observable (a skill is counted "consulted" only if its ``SKILL.md`` shows up in
the read log). Deeper skill attribution needs the harness to emit invocation
events — noted as future work, not faked here.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

# The *fallback* reading surface: governance docs + project memory the lab
# itself ships. Coverage over this set is the core attribution signal, but the
# set is only correct for a run that executed against the lab's own repository.
#
# A run under ``harness_source="native"`` never had these files — it had the
# target team's. Scoring it against this tuple measures the wrong repository, so
# callers that know the run pass its own surface (see
# ``harness_lab_app.governance.resolve_surface``) and this stays the default for
# a run whose condition cannot be resolved.
HARNESS_READING_SURFACE = (
    ".github/architecture.md",
    ".github/standards.md",
    ".github/domain-boundaries.md",
    "CLAUDE.md",
    "OPENCODE.md",
    "LOCAL_AGENT.md",
    "memory/context.md",
    "memory/learnings.md",
    "memory/patterns.md",
)

_PERMISSION_RE = re.compile(
    r"permission=(?P<action>[a-z]+) pattern=(?P<target>\"[^\"]*\"|\S+)"
)
_AGENT_RE = re.compile(r"\bagent=(?P<mode>[a-z]+)")
_SHELL_RE = re.compile(r'"shell tool using shell"')
_SKILL_PATH_RE = re.compile(r"(?:^|/)skills/(?P<name>[^/]+)/SKILL\.md")
# Agent modes that are noise, not real work modes.
_IGNORED_AGENT_MODES = {"title", "undefined"}


def _strip_app(path: str) -> str:
    return path.removeprefix("/app/").strip('"')


def _bash_command_name(target: str) -> str | None:
    """First token of a bash pattern (the command name)."""
    cleaned = target.strip().strip('"').lstrip("'")
    if not cleaned:
        return None
    token = cleaned.split()[0]
    # Env-assignment prefix (FOO=1 python ...) → take the next token.
    if "=" in token and not token.startswith("/"):
        parts = cleaned.split()
        for part in parts:
            if "=" not in part:
                token = part
                break
    return token.rsplit("/", 1)[-1]


def parse_logs(log_text: str) -> dict[str, Any]:
    """Extract observable tool/file/agent signals from concatenated step logs."""
    tools = {"read": 0, "edit": 0, "bash": 0, "shell": 0}
    files_read: set[str] = set()
    files_edited: set[str] = set()
    bash_commands: dict[str, int] = {}
    skills_consulted: set[str] = set()

    for match in _PERMISSION_RE.finditer(log_text):
        action = match.group("action")
        target = match.group("target")
        if action == "read":
            tools["read"] += 1
            path = _strip_app(target)
            files_read.add(path)
            skill = _SKILL_PATH_RE.search(path)
            if skill:
                skills_consulted.add(skill.group("name"))
        elif action in {"edit", "write"}:
            tools["edit"] += 1
            files_edited.add(_strip_app(target))
        elif action == "bash":
            tools["bash"] += 1
            name = _bash_command_name(target)
            if name:
                bash_commands[name] = bash_commands.get(name, 0) + 1

    # NB: opencode's "touching file" is the file-watcher accessing a file (it
    # fires on reads too), so it is NOT a reliable edit signal — only
    # permission=edit/write counts as an edit.
    tools["shell"] = len(_SHELL_RE.findall(log_text))

    agent_modes: dict[str, int] = {}
    for match in _AGENT_RE.finditer(log_text):
        mode = match.group("mode")
        if mode in _IGNORED_AGENT_MODES:
            continue
        agent_modes[mode] = agent_modes.get(mode, 0) + 1

    return {
        "tools": tools,
        "files_read": sorted(files_read),
        "files_edited": sorted(files_edited),
        "bash_commands": bash_commands,
        "agent_modes": agent_modes,
        "skills_consulted": sorted(skills_consulted),
    }


# claude tool names → the lab's tool buckets.
_CLAUDE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _normalize_event_path(path: str) -> str:
    """Container paths arrive absolute (/app/...); strip to repo-relative."""
    return path.removeprefix("/app/").lstrip("/") if path.startswith("/") else path


_WORKSPACE_PREFIX_RE = re.compile(r"^workspace/[^/]+/")


def _relative_to_workspace(path: str) -> str:
    """`workspace/<run>/x` → `x`; anything outside the workspace is kept as is.

    Keeping the lab-root path intact is deliberate: under an in-target
    condition a read of `CLAUDE.md` (the lab's) must *not* look like a read of
    the workspace's own, so it stays distinguishable in `files_read`.
    """
    return _WORKSPACE_PREFIX_RE.sub("", path, count=1)


def parse_claude_events(lines: list[str]) -> dict[str, Any]:
    """Extract tool/file/skill signals from claude stream-json event lines.

    Unlike the opencode permission-log parsing, these are first-class tool_use
    events, so skill *invocation* is directly observable (the Skill tool call),
    not just SKILL.md reads.
    """
    tools = {"read": 0, "edit": 0, "bash": 0, "shell": 0}
    files_read: set[str] = set()
    files_edited: set[str] = set()
    bash_commands: dict[str, int] = {}
    skills_consulted: set[str] = set()
    agent_modes: dict[str, int] = {}

    for raw in lines:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content", []):
            if block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            inp = block.get("input") or {}
            if name == "Read":
                tools["read"] += 1
                path = _normalize_event_path(str(inp.get("file_path", "")))
                if path:
                    files_read.add(path)
                    skill = _SKILL_PATH_RE.search(path)
                    if skill:
                        skills_consulted.add(skill.group("name"))
            elif name in _CLAUDE_EDIT_TOOLS:
                tools["edit"] += 1
                target = inp.get("file_path") or inp.get("notebook_path")
                if target:
                    files_edited.add(_normalize_event_path(str(target)))
            elif name == "Bash":
                tools["bash"] += 1
                command = _bash_command_name(str(inp.get("command", "")))
                if command:
                    bash_commands[command] = bash_commands.get(command, 0) + 1
            elif name == "Skill":
                skill_name = str(inp.get("skill", "")).strip()
                if skill_name:
                    skills_consulted.add(skill_name)
            elif name in {"Task", "Agent"}:
                mode = str(inp.get("subagent_type", "agent")).strip() or "agent"
                agent_modes[mode] = agent_modes.get(mode, 0) + 1

    return {
        "tools": tools,
        "files_read": sorted(files_read),
        "files_edited": sorted(files_edited),
        "bash_commands": bash_commands,
        "agent_modes": agent_modes,
        "skills_consulted": sorted(skills_consulted),
    }


def _assemble(
    parsed: dict[str, Any],
    skills_available: list[str] | None,
    steps_parsed: int,
    skills_note: str,
    reading_surface: tuple[str, ...] | None = None,
    governance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    surface: tuple[str, ...] = HARNESS_READING_SURFACE
    if reading_surface is not None:
        surface = tuple(reading_surface)
    read_set = set(parsed["files_read"]) | set(parsed["files_edited"])
    # A run whose harness lives in the workspace (native, injected, bare —
    # ADR-0031/0049) reads its rulebook at `workspace/<run>/CLAUDE.md`, while
    # the surface lists it as `CLAUDE.md`. Without this the two never matched:
    # an in-target run scored 0/N on its own governance, and — worse — a read
    # of the *lab's* `/app/CLAUDE.md` was the one path that did match, so the
    # only read that signalled contamination counted as compliance.
    if governance and governance.get("source") != "lab_root":
        read_set = {_relative_to_workspace(path) for path in read_set}
    docs_read = [doc for doc in surface if doc in read_set]
    skills_available = sorted(skills_available or [])

    harness_usage = {
        "governance_docs_available": list(surface),
        "governance_docs_read": docs_read,
        "governance_coverage": f"{len(docs_read)}/{len(surface)}",
        "skills_available": skills_available,
        "skills_consulted": parsed["skills_consulted"],
        "skills_note": skills_note,
    }
    # Which repository the denominator came from. Without it a coverage figure
    # is unreadable: 2/9 against the lab's docs and 2/9 against a client's are
    # different measurements that would otherwise render identically.
    if governance:
        harness_usage["governance_source"] = governance.get("source", "")
        harness_usage["governance_fingerprint"] = governance.get("fingerprint", "")
        harness_usage["harness_source"] = governance.get("harness_source", "")
        if governance.get("warning"):
            harness_usage["governance_warning"] = governance["warning"]
    tools = parsed["tools"]
    summary = (
        f"read {len(docs_read)}/{len(surface)} governance docs; "
        f"{tools['edit']} edits, {tools['read']} reads, {tools['bash']} bash; "
        f"modes: {','.join(sorted(parsed['agent_modes'])) or 'n/a'}"
    )
    return {
        "tools": tools,
        "files_read": parsed["files_read"],
        "files_edited": parsed["files_edited"],
        "bash_commands": parsed["bash_commands"],
        "agent_modes": parsed["agent_modes"],
        "harness_usage": harness_usage,
        "steps_parsed": steps_parsed,
        "summary": summary,
    }


def build_attribution(
    log_text: str,
    *,
    skills_available: list[str] | None = None,
    steps_parsed: int = 0,
    reading_surface: tuple[str, ...] | None = None,
    governance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full attribution from opencode permission logs."""
    return _assemble(
        parse_logs(log_text),
        skills_available,
        steps_parsed,
        skills_note=(
            "skill invocation is context-injected, not file-read; "
            "'consulted' only counts SKILL.md reads and is a lower bound"
        ),
        reading_surface=reading_surface,
        governance=governance,
    )


def build_attribution_from_events(
    event_lines: list[str],
    *,
    skills_available: list[str] | None = None,
    steps_parsed: int = 0,
    reading_surface: tuple[str, ...] | None = None,
    governance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full attribution from claude stream-json events (first-class tool calls)."""
    return _assemble(
        parse_claude_events(event_lines),
        skills_available,
        steps_parsed,
        skills_note=(
            "events-based: 'consulted' counts real Skill-tool invocations "
            "plus SKILL.md reads"
        ),
        reading_surface=reading_surface,
        governance=governance,
    )


def read_step_logs(run_dir: Path) -> tuple[str, int]:
    """Concatenate the run's step logs; fall back to opencode.log."""
    steps_dir = run_dir / "steps"
    logs = sorted(steps_dir.glob("step-*.log")) if steps_dir.is_dir() else []
    if not logs:
        fallback = run_dir / "opencode.log"
        if fallback.exists():
            return _safe_read(fallback), 0
        return "", 0
    text = "\n".join(_safe_read(path) for path in logs)
    return text, len(logs)


def read_step_events(run_dir: Path) -> tuple[list[str], int]:
    """Collect claude stream-json event lines across steps (empty when absent)."""
    steps_dir = run_dir / "steps"
    files = sorted(steps_dir.glob("step-*.events.jsonl")) if steps_dir.is_dir() else []
    single = run_dir / "opencode.events.jsonl"
    if single.exists():
        files.append(single)
    lines: list[str] = []
    for path in files:
        lines.extend(_safe_read(path).splitlines())
    return lines, len([f for f in files if f.name.startswith("step-")])


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""
