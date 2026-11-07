"""Deterministic checks of a repository's engineering harness (ADR-0065).

The audit's model passes *describe* a harness; these checks *decide* a handful
of things a reader can verify in seconds and a model routinely gets wrong:
whether the verification target can fail, whether a hook can block anything,
how much instruction text every session loads, whether skills are well formed.

Pure over ``paths`` and ``read`` — the same inputs ``collect_evidence`` takes —
so a check never runs a process, opens a network socket or calls a model, and
each one is argued with in a test against a few strings. Every check returns
the same shape: an id, a status, the evidence it saw, why it matters, and the
fix. A check that has nothing to look at says ``not_applicable`` rather than
passing: absence is not compliance (ADR-0048).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from harness_lab_app import governance_paths as gp

PASS = "pass"
WARN = "warn"
FAIL = "fail"
NOT_APPLICABLE = "not_applicable"

_ORDER = {FAIL: 0, WARN: 1, PASS: 2, NOT_APPLICABLE: 3}

# Targets and scripts an agent is told to verify with.
_VERIFY_TARGETS = ("test", "check", "ci", "verify", "lint", "typecheck")
# A shell fallback that turns any failure into exit 0.
_SWALLOW = re.compile(r"\|\|\s*(true\b|:|echo\b|exit\s+0\b)")
# A fallback only hides a failing test when the command it guards *verifies*
# something; `command -v uv || echo "install uv"` guards a precondition.
_VERIFIER = re.compile(
    r"\b(pytest|py\.test|unittest|tox|nox|ruff|mypy|pyright|flake8|pylint|bandit|"
    r"jest|vitest|mocha|playwright|cypress|eslint|tsc|go\s+test|cargo\s+test|"
    r"mvn|gradle|rspec|phpunit|dotnet\s+test|npm\s+(run\s+)?test|"
    r"pnpm\s+(run\s+)?test|yarn\s+(run\s+)?test|make\s+(test|check|ci)\b)"
)
# Recipe lines that rewrite the tree while "verifying".
_MUTATING = re.compile(
    r"\b(make\s+(format|fix)\b|ruff\s+format(?!\s+--check)|black\s+(?!--check)\S|"
    r"prettier\s+--write|eslint\s+[^\n]*--fix|ruff\s+check\s+[^\n]*--fix)"
)
# A rule line: one or more targets, a single colon (not `:=` / `::=`), then
# prerequisites and an optional inline `; recipe`.
_MAKE_RULE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_./ -]*?)\s*:(?![:=])(.*)$")

# Above these, a rules file stops being a map and becomes a manual every
# session pays for. 200 lines is the size Claude Code's own guidance gives for a
# CLAUDE.md; the token figure is the same budget at ~4 characters per token.
_RULES_LINE_BUDGET = 200
_RULES_TOKEN_BUDGET = 2500
# Every rules file the audit's governance classifier recognises (gp.RULES_FILES):
# a repository governed only by `.cursorrules` or `OPENCODE.md` has a rules file.
_ALWAYS_LOADED = gp.RULES_FILES

# A hook can only block by printing a decision object or exiting 2.
_BLOCKING = re.compile(
    r"\"decision\"|'decision'|\bexit\s+2\b|sys\.exit\(\s*2\s*\)|process\.exit\(\s*2\s*\)"
)
_SCRIPT_SUFFIXES = (".sh", ".bash", ".py", ".js", ".ts")
# Text from the audited repository is untrusted: bounded before any pattern
# runs on it, and flattened to one line before it reaches evidence, a model
# prompt or the Markdown report.
_MAX_COMMAND_CHARS = 2_000
_MAX_EVIDENCE_ITEM_CHARS = 160
# A hook script is scanned for its blocking forms only within its first part,
# the same cap the audit applies to hook texts it shows the model.
_MAX_SCRIPT_CHARS = 20_000

_SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

_UNPINNED = re.compile(r"@latest\b|^[@\w./-]+$")

Read = Callable[[str], str]


def _row(
    check_id: str,
    title: str,
    status: str,
    evidence: str,
    why: str,
    fix: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "evidence": evidence,
        "why": why,
        "fix": fix if status in (FAIL, WARN) else "",
        "details": details or {},
    }


def _safe_read(read: Read, path: str) -> str:
    try:
        return read(path) or ""
    except Exception:
        return ""


def _make_rules(text: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Each target's own recipe lines and its prerequisites.

    Handles multi-target rules (`lint test:`), inline recipes (`t: ; cmd`),
    continuation lines and conditional blocks inside a recipe. It is a reader of
    the common shapes, not a make interpreter: `include` and computed targets
    are not followed.
    """
    recipes: dict[str, list[str]] = {}
    prereqs: dict[str, list[str]] = {}
    current: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = pending + raw
        if line.endswith("\\"):
            pending = line[:-1] + " "
            continue
        pending = ""
        if line.startswith("\t"):
            for target in current:
                recipes[target].append(line.strip())
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.split()[0] in ("ifeq", "ifneq", "ifdef", "ifndef", "else", "endif"):
            continue  # conditionals inside a recipe keep the current rule open
        match = _MAKE_RULE.match(line)
        if not match or match.group(1).startswith("."):
            current = []
            continue
        current = match.group(1).split()
        deps, _, inline = match.group(2).partition(";")
        for target in current:
            recipes.setdefault(target, [])
            prereqs.setdefault(target, []).extend(deps.split())
            if inline.strip():
                recipes[target].append(inline.strip())
    return recipes, prereqs


def _make_recipes(text: str) -> dict[str, list[str]]:
    """Each target's recipe, with its prerequisites' recipes folded in.

    `check: lint test` is judged by what `lint` and `test` run; the expansion
    follows prerequisites transitively and stops at a cycle.
    """
    recipes, prereqs = _make_rules(text)

    def expand(target: str, seen: set[str]) -> list[str]:
        if target in seen:
            return []
        seen.add(target)
        lines: list[str] = []
        for dep in prereqs.get(target, []):
            lines.extend(expand(dep, seen))
        lines.extend(recipes.get(target, []))
        return lines

    return {target: expand(target, set()) for target in recipes}


def _swallows(line: str) -> bool:
    """A fallback that turns a *verifying* command's failure into success."""
    for match in _SWALLOW.finditer(line):
        guarded = line[: match.start()].rsplit("&&", 1)[-1].rsplit(";", 1)[-1]
        if _VERIFIER.search(guarded):
            return True
    return False


def _verification_commands(paths: list[str], read: Read) -> dict[str, list[str]]:
    """``{"Makefile:test": [lines], "package.json:test": [cmd]}`` for verify targets.

    Ordered by :data:`_VERIFY_TARGETS` — the targets an agent is told to run
    first — so evidence leads with ``test``/``check`` rather than ``lint``.
    """
    found = _unordered_verification_commands(paths, read)
    rank = {target: index for index, target in enumerate(_VERIFY_TARGETS)}
    return dict(
        sorted(found.items(), key=lambda item: rank.get(item[0].split(":", 1)[1], 99))
    )


def _unordered_verification_commands(
    paths: list[str], read: Read
) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    path_set = set(paths)
    if "Makefile" in path_set:
        for target, lines in _make_recipes(_safe_read(read, "Makefile")).items():
            if target in _VERIFY_TARGETS:
                found[f"Makefile:{target}"] = lines
    if "package.json" in path_set:
        try:
            scripts = json.loads(_safe_read(read, "package.json") or "{}").get(
                "scripts"
            )
        except (json.JSONDecodeError, AttributeError):
            scripts = None
        if isinstance(scripts, dict):
            for name, command in scripts.items():
                if name in _VERIFY_TARGETS and isinstance(command, str):
                    found[f"package.json:{name}"] = [command]
    return found


def check_verify_exit_code(paths: list[str], read: Read) -> dict[str, Any]:
    title = "Verification can fail"
    why = (
        "Agents are told to verify with this target. If a failing test still "
        "exits 0, every 'tests pass' the agent reports is unverified."
    )
    commands = _verification_commands(paths, read)
    if not commands:
        return _row(
            "verify_target_exit_code",
            title,
            WARN,
            "No test/check/ci target in a Makefile or package.json.",
            why,
            "Add one read-only target (e.g. `make check`) that runs the tests and "
            "lints and fails when they fail, and point the agent rules at it.",
        )
    swallowing = [
        f"`{source}`: `{_one_line(line)}`"
        for source, lines in commands.items()
        for line in lines
        if _swallows(line)
    ]
    if swallowing:
        return _row(
            "verify_target_exit_code",
            title,
            FAIL,
            "; ".join(swallowing[:3]),
            why,
            "Drop the `|| echo …` / `|| true` fallback so the target exits non-zero "
            "when tests fail. If 'no tests collected' must pass, handle pytest's "
            "exit code 5 explicitly instead of every failure.",
        )
    return _row(
        "verify_target_exit_code",
        title,
        PASS,
        "Checked " + ", ".join(f"`{name}`" for name in sorted(commands)) + ".",
        why,
    )


def check_verify_read_only(paths: list[str], read: Read) -> dict[str, Any]:
    title = "Verification does not rewrite the tree"
    why = (
        "A target that formats or auto-fixes while 'testing' changes the code the "
        "agent is checking, hides lint failures and leaves unexplained diffs."
    )
    commands = _verification_commands(paths, read)
    if not commands:
        return _row(
            "verify_target_read_only", title, NOT_APPLICABLE, "No verify target.", why
        )
    mutating = [
        f"`{source}`: `{_one_line(line)}`"
        for source, lines in commands.items()
        for line in lines
        if _MUTATING.search(line)
    ]
    if mutating:
        return _row(
            "verify_target_read_only",
            title,
            WARN,
            "; ".join(mutating[:3]),
            why,
            "Keep `format`/`fix` as separate local targets and make the verify "
            "target check-only (`ruff format --check`, `prettier --check`).",
        )
    return _row(
        "verify_target_read_only", title, PASS, "No formatter or fixer runs.", why
    )


def _hook_commands(paths: list[str], read: Read) -> list[tuple[str, str]]:
    """``[(event, command)]`` from every Claude settings file present."""
    pairs: list[tuple[str, str]] = []
    path_set = set(paths)
    for name in gp.CLAUDE_SETTINGS:
        if name not in path_set:
            continue
        try:
            data = json.loads(_safe_read(read, name) or "{}")
        except json.JSONDecodeError:
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks, dict):
            continue
        for event, groups in hooks.items():
            for group in groups if isinstance(groups, list) else []:
                inner = group.get("hooks") if isinstance(group, dict) else None
                for hook in inner if isinstance(inner, list) else []:
                    if isinstance(hook, dict) and hook.get("type") == "command":
                        pairs.append((str(event), str(hook.get("command", ""))))
    return pairs


def _one_line(text: str, limit: int = _MAX_EVIDENCE_ITEM_CHARS) -> str:
    """Repository text made safe to quote: one line, bounded, no backticks."""
    flat = " ".join(str(text).split()).replace("`", "'")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _script_path(command: str, paths: list[str]) -> str | None:
    """The repository script a hook command runs, if any.

    A plain token scan — linear in the command's length. The command is read
    from the audited repository, so no backtracking pattern runs on it.
    """
    path_set = set(paths)
    for token in command[:_MAX_COMMAND_CHARS].split():
        raw = token.strip("\"'")
        if not raw.endswith(_SCRIPT_SUFFIXES):
            continue
        for prefix in ("$CLAUDE_PROJECT_DIR/", "${CLAUDE_PROJECT_DIR}/", "./"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
        if raw in path_set:
            return raw
    return None


def check_hooks_can_block(paths: list[str], read: Read) -> dict[str, Any]:
    title = "Hooks can enforce, not only remind"
    why = (
        "Prose rules are advisory: a model can skip them. A hook enforces only if "
        "it can block — by printing a decision object or exiting 2. A hook that "
        "prints a reminder and exits 0 is shown to the user, not acted on."
    )
    commands = _hook_commands(paths, read)
    if not commands:
        return _row(
            "hooks_can_block",
            title,
            NOT_APPLICABLE,
            "No command hooks declared in .claude/settings.json.",
            why,
        )
    blocking: list[str] = []
    advisory: list[str] = []
    for event, command in commands:
        script = _script_path(command, paths)
        text = (_safe_read(read, script) if script else command)[:_MAX_SCRIPT_CHARS]
        label = f"{_one_line(event, 40)} → `{_one_line(script or command)}`"
        (blocking if _BLOCKING.search(text) else advisory).append(label)
    details = {"blocking": blocking, "advisory": advisory}
    if blocking:
        return _row(
            "hooks_can_block",
            title,
            PASS,
            f"{len(blocking)} hook(s) can block: " + "; ".join(blocking[:3]),
            why,
            details=details,
        )
    return _row(
        "hooks_can_block",
        title,
        WARN,
        "Every hook is advisory: " + "; ".join(advisory[:4]),
        why,
        "Turn the most important rule into a blocking hook — e.g. a Stop hook that "
        'returns `{"decision": "block"}` when src/ or tests/ changed and the '
        "fast checks were not run since the last edit.",
        details=details,
    )


def check_instruction_budget(paths: list[str], read: Read) -> dict[str, Any]:
    title = "Always-loaded instructions stay a map"
    why = (
        "Every session pays for the rules file before it reads the task. Long "
        "files crowd out the task and research finds they rarely raise success; "
        "a short map that points to docs and skills does the same job."
    )
    path_set = set(paths)
    present = [name for name in _ALWAYS_LOADED if name in path_set]
    if not present:
        return _row(
            "instruction_budget",
            title,
            FAIL,
            "No agent rules file (CLAUDE.md, AGENTS.md, GEMINI.md, OPENCODE.md, "
            "Copilot or Cursor instructions).",
            why,
            "Add a short AGENTS.md (with CLAUDE.md importing it): what the repo is, "
            "how to verify a change, where the rules live.",
        )
    files: dict[str, dict[str, int]] = {}
    over: list[str] = []
    over_lines = over_tokens = False
    for name in present:
        text = _safe_read(read, name)
        lines = len(text.splitlines())
        tokens = len(text) // 4
        files[name] = {"lines": lines, "tokens": tokens}
        if lines > _RULES_LINE_BUDGET or tokens > _RULES_TOKEN_BUDGET:
            over.append(f"`{name}` {lines} lines / ~{tokens} tokens")
            over_lines = over_lines or lines > _RULES_LINE_BUDGET
            over_tokens = over_tokens or tokens > _RULES_TOKEN_BUDGET
    evidence = ", ".join(
        f"`{name}` {info['lines']} lines / ~{info['tokens']} tokens"
        for name, info in files.items()
    )
    if over:
        return _row(
            "instruction_budget",
            title,
            WARN,
            "; ".join(over),
            why,
            "Trim to "
            + " and ".join(
                part
                for part, hit in (
                    (f"≤{_RULES_LINE_BUDGET} lines", over_lines),
                    (f"≤~{_RULES_TOKEN_BUDGET} tokens", over_tokens),
                )
                if hit
            )
            + ": keep what is non-obvious about this repo, move procedures into "
            "skills and reference docs, delete rules a linter already enforces.",
            details={"files": files},
        )
    return _row(
        "instruction_budget", title, PASS, evidence, why, details={"files": files}
    )


def _frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER.match(text.lstrip("﻿"))
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


_FLAT_SKILL = re.compile(r"^\.github/skills/([^/]+)\.md$")
# A symlinked file read through git comes back as its target path: one line,
# no newline, ending in .md. Such a copy is a projection, not a source.
_LINK_TARGET = re.compile(r"^[\w./-]+\.md$")


def _skill_sources(paths: list[str], read: Read) -> dict[str, str]:
    """``{skill name: text}`` — one entry per skill, wherever it is projected.

    Governed templates keep one source (a flat ``.github/skills/<name>.md`` or a
    ``SKILL.md`` folder) and project symlinked copies into each tool's folder.
    The first readable copy of a name wins; link stubs are skipped so a
    projection is never mistaken for a skill without frontmatter.
    """
    candidates: list[tuple[str, str]] = []
    for path in sorted(paths):
        flat = _FLAT_SKILL.match(path)
        if flat and flat.group(1).lower() != "readme":
            candidates.append((flat.group(1), path))
        # `is_skill_path` covers plugin skills too (`plugins/<p>/skills/<s>/`).
        elif gp.is_skill_path(path):
            candidates.append((path.rsplit("/", 2)[-2], path))
    sources: dict[str, str] = {}
    for name, path in candidates:
        if name in sources:
            continue
        text = _safe_read(read, path)
        if not text or (_LINK_TARGET.match(text.strip()) and "\n" not in text.strip()):
            continue
        sources[name] = text
    return sources


def check_skill_frontmatter(paths: list[str], read: Read) -> dict[str, Any]:
    title = "Skills are discoverable"
    why = (
        "An agent decides whether to load a skill from its name and description "
        "alone. A missing description means the skill is never chosen; names "
        "outside the Agent Skills format are rejected by strict validators."
    )
    sources = _skill_sources(paths, read)
    skills = sorted(sources)
    if not skills:
        return _row(
            "skill_frontmatter", title, NOT_APPLICABLE, "No SKILL.md files.", why
        )
    broken: list[str] = []
    off_format: list[str] = []
    for folder in skills:
        meta = _frontmatter(sources[folder])
        name = meta.get("name", "")
        if not name or not meta.get("description"):
            broken.append(folder)
        elif not _SKILL_NAME.match(name):
            off_format.append(name)
    details = {"skills": len(skills), "broken": broken, "off_format": off_format}
    if broken:
        return _row(
            "skill_frontmatter",
            title,
            FAIL,
            f"{len(broken)} of {len(skills)} skill(s) lack a name or description: "
            + ", ".join(broken[:5]),
            why,
            "Give each SKILL.md frontmatter with `name` and a `description` that "
            "says when to use it (the trigger), not what it is.",
            details=details,
        )
    if off_format:
        return _row(
            "skill_frontmatter",
            title,
            WARN,
            f"{len(off_format)} name(s) outside lowercase-hyphen format: "
            + ", ".join(off_format[:5]),
            why,
            "Rename to lowercase letters, digits and hyphens (e.g. `verify-changes`).",
            details=details,
        )
    return _row(
        "skill_frontmatter",
        title,
        PASS,
        f"{len(skills)} skill(s), all with name and description.",
        why,
        details=details,
    )


def check_mcp_pinned(paths: list[str], read: Read) -> dict[str, Any]:
    title = "MCP servers are pinned"
    why = (
        "An MCP server on `@latest` runs whatever was published last, with the "
        "agent's permissions. Pinning makes an upgrade a reviewed change."
    )
    if ".mcp.json" not in set(paths):
        return _row("mcp_pinned", title, NOT_APPLICABLE, "No .mcp.json.", why)
    try:
        servers = (
            json.loads(_safe_read(read, ".mcp.json") or "{}").get("mcpServers") or {}
        )
    except (json.JSONDecodeError, AttributeError):
        servers = {}
    unpinned: list[str] = []
    packaged = remote = 0
    for name, spec in servers.items() if isinstance(servers, dict) else []:
        if not isinstance(spec, dict):
            continue
        if spec.get("url") and not spec.get("command"):
            remote += 1
            continue
        package = _launched_package(spec)
        if package is None:
            continue
        packaged += 1
        if not _is_pinned(package):
            unpinned.append(f"{_one_line(name, 40)} (`{_one_line(package, 80)}`)")
    remote_note = f"; {remote} remote (url) server(s), not a package" if remote else ""
    if unpinned:
        return _row(
            "mcp_pinned",
            title,
            WARN,
            "Unpinned: " + ", ".join(unpinned) + remote_note,
            why,
            "Pin each package to an exact version (`pkg@1.2.3` for npx/bunx, "
            "`pkg==1.2.3` for uvx/pipx).",
        )
    if packaged == 0:
        return _row(
            "mcp_pinned",
            title,
            NOT_APPLICABLE,
            f"No package-launched MCP server{remote_note}.",
            why,
        )
    return _row(
        "mcp_pinned",
        title,
        PASS,
        f"{packaged} package server(s) pinned{remote_note}.",
        why,
    )


# Launchers that fetch and run a package by name, and the flags that may come
# between the launcher and the package.
_LAUNCHERS = {"npx", "bunx", "uvx", "pipx", "dlx"}
_LAUNCHER_FLAGS_WITH_VALUE = {"--from", "--with", "-p", "--package", "--python"}


def _launched_package(spec: dict[str, Any]) -> str | None:
    """The package an MCP server launches, from `command`+`args` or `sh -c "…"`."""
    command = str(spec.get("command") or "")
    args = [str(a) for a in spec.get("args") or [] if isinstance(a, str)]
    tokens = [command, *args]
    if command in ("sh", "bash") and "-c" in args:
        script = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
        tokens = script[:_MAX_COMMAND_CHARS].split()
    for index, token in enumerate(tokens):
        base = token.rsplit("/", 1)[-1]
        if base not in _LAUNCHERS and not (
            base == "run" and index and tokens[index - 1].endswith("pipx")
        ):
            continue
        rest = tokens[index + 1 :]
        skip = False
        for item in rest:
            if skip:
                skip = False
                continue
            if item in _LAUNCHER_FLAGS_WITH_VALUE:
                skip = True
                continue
            if item.startswith("-") or item == "run":
                continue
            return item.strip("\"'")
    return None


def _is_pinned(package: str) -> bool:
    """`name@1.2.3`, `@scope/name@1.2.3` or `name==1.2.3`; never `@latest`."""
    if "==" in package:
        return bool(package.split("==", 1)[1])
    body = package[1:] if package.startswith("@") else package
    if "@" not in body:
        return False
    version = body.rsplit("@", 1)[1]
    return bool(version) and version not in ("latest", "next") and version[0].isdigit()


def check_ci_runs_gate(paths: list[str], read: Read) -> dict[str, Any]:
    title = "CI runs the same gate the agent runs"
    why = (
        "The agent's local verification only counts if CI enforces the same "
        "target on every change; otherwise skipped checks reach main."
    )
    workflows = [path for path in paths if path.startswith(gp.CI_ROOTS)]
    if not workflows:
        return _row(
            "ci_runs_gate",
            title,
            WARN,
            "No CI workflow under .github/workflows/.",
            why,
            "Add a workflow that runs the read-only gate (e.g. `make check`) on "
            "every pull request.",
        )
    targets = {name.split(":", 1)[1] for name in _verification_commands(paths, read)}
    text = "\n".join(_safe_read(read, path) for path in workflows)
    running = sorted(
        target
        for target in targets
        if re.search(
            rf"\b(make|npm run|pnpm|yarn)\s+(run\s+)?{re.escape(target)}\b", text
        )
    )
    if running:
        return _row(
            "ci_runs_gate",
            title,
            PASS,
            f"{len(workflows)} workflow(s); CI runs "
            + ", ".join(f"`{t}`" for t in running)
            + ".",
            why,
        )
    return _row(
        "ci_runs_gate",
        title,
        WARN,
        f"{len(workflows)} workflow(s), none runs a local verify target.",
        why,
        "Call the same target from CI that the rules tell the agent to run.",
    )


def check_harness_version(paths: list[str], read: Read) -> dict[str, Any]:
    title = "The harness version is declared"
    why = (
        "Without a declared template version nobody can tell which harness a "
        "result came from, or whether an upgrade changed anything."
    )
    for name in (".harness-version.yaml", ".template-version"):
        if name in set(paths):
            text = _safe_read(read, name)
            match = re.search(r"ref:\s*(\S+)", text) or re.search(r"(\S+)", text)
            version = match.group(1) if match else "?"
            return _row(
                "harness_version_declared",
                title,
                PASS,
                f"`{name}` declares `{version}`.",
                why,
            )
    return _row(
        "harness_version_declared",
        title,
        WARN,
        "No .harness-version.yaml or .template-version.",
        why,
        "Record the template release the harness came from (e.g. "
        "`make harness-adopt REF=vX.Y.Z` in an ml-python-base project).",
    )


CHECKS: tuple[Callable[[list[str], Read], dict[str, Any]], ...] = (
    check_verify_exit_code,
    check_verify_read_only,
    check_hooks_can_block,
    check_instruction_budget,
    check_skill_frontmatter,
    check_mcp_pinned,
    check_ci_runs_gate,
    check_harness_version,
)


def run_checks(paths: list[str], read: Read) -> list[dict[str, Any]]:
    """Every check, in a fixed order, never raising."""
    return [check(paths, read) for check in CHECKS]


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts per status and the checks ordered failures first."""
    counts = dict.fromkeys((PASS, WARN, FAIL, NOT_APPLICABLE), 0)
    for row in checks:
        counts[row["status"]] += 1
    ordered = sorted(checks, key=lambda row: _ORDER[row["status"]])
    return {**counts, "ordered": ordered}
