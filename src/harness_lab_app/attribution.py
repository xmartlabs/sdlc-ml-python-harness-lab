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

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

# The harness "reading surface": governance docs + project memory the agent is
# expected to consult. Coverage over this set is the core attribution signal.
HARNESS_READING_SURFACE = (
    ".github/architecture.md",
    ".github/standards.md",
    ".github/domain-boundaries.md",
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


def build_attribution(
    log_text: str,
    *,
    skills_available: list[str] | None = None,
    steps_parsed: int = 0,
) -> dict[str, Any]:
    """Full attribution: parsed signals + harness read-coverage cross-reference."""
    parsed = parse_logs(log_text)
    read_set = set(parsed["files_read"]) | set(parsed["files_edited"])
    docs_read = [doc for doc in HARNESS_READING_SURFACE if doc in read_set]
    skills_available = sorted(skills_available or [])

    harness_usage = {
        "governance_docs_available": list(HARNESS_READING_SURFACE),
        "governance_docs_read": docs_read,
        "governance_coverage": f"{len(docs_read)}/{len(HARNESS_READING_SURFACE)}",
        "skills_available": skills_available,
        "skills_consulted": parsed["skills_consulted"],
        "skills_note": (
            "skill invocation is context-injected, not file-read; "
            "'consulted' only counts SKILL.md reads and is a lower bound"
        ),
    }
    tools = parsed["tools"]
    summary = (
        f"read {len(docs_read)}/{len(HARNESS_READING_SURFACE)} governance docs; "
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


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""
