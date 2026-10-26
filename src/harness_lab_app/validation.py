"""Objective validation and multidimensional scoring of completed runs.

This turns "did the agent produce good code?" into separable, measurable
dimensions instead of a single 0-10 number:

- objective: run the case's validation commands (smoke / test suite) against the
  produced workspace and record pass/fail — the objective floor.
- cost: tokens and money (parsed from ``usage.json``).
- effort: steps and human interventions.
- subjective: the manual/LLM functional score (0-10) and optional rubric.
- composite: an explicit roll-up (documented, not magic).

The pure functions here (parsing, spec building, composite) are unit-tested;
actually executing the commands is done by ``ValidationService`` through the
run executor so it runs in the same container isolation as a run.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_lab_app.config import get_settings
from harness_lab_app.models import CaseType
from harness_lab_app.runner_capabilities import (
    declared_runtime_for_binary,
    shell_for_command,
)

# GNU `timeout` exists in the runner container (Linux) but not on a macOS host
# worker (subprocess backend). Make case commands portable across both.
_TIMEOUT_RE = re.compile(r"\btimeout\s+(\d+)\s+")
# The same prefix after rewriting, so the budget can be read back out.
_TIMEOUT_BUDGET_RE = re.compile(r"\btimeout\s+(?:-k\s+\d+\s+)?(\d+)\s+")

# `timeout N` sends SIGTERM and then waits forever. A child that survives
# SIGTERM therefore survives `timeout` — which is exactly what hung the fourth
# run of experiment `...-on--2`: SDL2 converts SIGTERM into an SDL_QUIT *event*,
# so a pygame program with a second event loop consumed the quit and kept
# running. `-k` escalates to SIGKILL, which nothing can catch.
_KILL_GRACE_SECONDS = 5

# Backstop for a command that carries no `timeout` prefix of its own, or whose
# `timeout` is itself wedged. Generous: a real test suite is allowed to be slow.
DEFAULT_VALIDATION_DEADLINE_SECONDS = 900
_WATCHDOG_MARGIN_SECONDS = 30


def _portable_timeout(command: str) -> str:
    """Rewrite a `timeout <N>` prefix so it escalates, and so it runs on macOS.

    Two jobs in one pass. It adds `-k` so the wrapper can actually kill what it
    started, and it prefers `gtimeout` (brew coreutils) where GNU timeout is
    absent; with neither available the wrapper is dropped, since the smoke
    commands self-exit (e.g. tetris under BENCH_SMOKE) and the guard is a
    backstop rather than the mechanism.
    """
    binary = "timeout" if shutil.which("timeout") else None
    if binary is None and shutil.which("gtimeout"):
        binary = "gtimeout"
    if binary is None:
        return _TIMEOUT_RE.sub("", command)
    return _TIMEOUT_RE.sub(
        lambda m: f"{binary} -k {_KILL_GRACE_SECONDS} {m.group(1)} ", command
    )


def command_deadline_seconds(
    rendered: str, default: int = DEFAULT_VALIDATION_DEADLINE_SECONDS
) -> int:
    """How long the caller may block on one validation command before killing it.

    A command that states its own `timeout N` budget gets that plus room for the
    kill escalation; anything else gets the default. This is the outer guard —
    the one that fires when `timeout` itself cannot help, because the wrapper
    was dropped for portability or because the process is stuck in the kernel.
    """
    match = _TIMEOUT_BUDGET_RE.search(rendered)
    if match:
        return int(match.group(1)) + _KILL_GRACE_SECONDS + _WATCHDOG_MARGIN_SECONDS
    return default


# Exit codes that mean "we did not get an answer", not "the answer was wrong":
# 124 is GNU timeout's own status, 137 and 143 are SIGKILL and SIGTERM (128+n),
# -1 is this process failing to launch the command at all, and 127 is the shell
# reporting that the command itself does not exist. ADR-0046 kept infrastructure
# out of an experiment's verdict; these keep it out of a run's capability score,
# which is where it entered in experiment `...-on--2`.
INCONCLUSIVE_EXIT_CODES = frozenset({-1, 124, 137, 143, 127})

#: Why a check settled nothing, keyed by the reason code stored on it.
INCONCLUSIVE_LABELS = {
    "timeout": "timed out",
    "killed": "killed",
    "not_launched": "never launched",
    "not_found": "command not found",
    "no_network": "no network",
    "missing_dependency": "grader dependency missing",
}

# `No module named 'x'` from Python's import system. Read only for modules the
# agent was not supposed to write: a missing `clinic` is the agent's failure,
# a missing `pandas` is the grader's environment (ADR-0071).
_MISSING_MODULE_RE = re.compile(r"No module named '([A-Za-z0-9_]+)(?:\.[^']*)?'")

# A requirement the setup step will pass to pip: a distribution name, optional
# extras, and an optional version clause. Anything else is dropped rather than
# quoted into a shell — the list is case data, not a free-form command.
_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?"
    r"((==|>=|<=|~=|!=|>|<)[A-Za-z0-9.*+!_-]+(,(==|>=|<=|~=|!=|>|<)[A-Za-z0-9.*+!_-]+)*)?$"
)

#: Where a run's grader dependencies are installed, relative to the lab root.
VALIDATION_DEPS_DIR = "validation-deps"

_EXIT_CODE_REASONS = {
    -1: "not_launched",
    124: "timeout",
    137: "killed",
    143: "killed",
    127: "not_found",
}

# A missing binary, as each layer reports it. Anchored to end of line because
# "not found" is ordinary prose in test output — an unanchored match would call
# a genuine assertion failure infrastructure. `Error 127` is how make reports a
# recipe command that does not exist; make's own exit status is 2, so the code
# alone never catches it, and that is exactly how a lab-root `make test` whose
# `format` target could not find ruff was stored as the agent failing.
_NOT_FOUND_RE = re.compile(
    r"(?m)(?:command not found|: not found)\s*$|\bError 127\b", re.IGNORECASE
)

# A sandbox with `network_mode="none"` (the hardened validate lane) as every
# resolver in it phrases the same fact.
_NO_NETWORK_RE = re.compile(
    r"Temporary failure in name resolution"
    r"|failed to lookup address information"
    r"|dns error"
    r"|Could not resolve host"
    r"|Name or service not known"
    r"|Network is unreachable"
    r"|nodename nor servname provided",
    re.IGNORECASE,
)


def workspace_modules(workspace: Path) -> set[str]:
    """Top-level module and package names the agent's workspace provides."""
    names: set[str] = set()
    try:
        for entry in workspace.iterdir():
            if entry.suffix == ".py":
                names.add(entry.stem)
            elif entry.is_dir() and (entry / "__init__.py").exists():
                names.add(entry.name)
    except OSError:
        pass
    return names


def validation_requirements(case: Any) -> list[str]:
    """The pip requirements a case's graders need, validated, in order."""
    meta = getattr(case, "metadata_json", None) or {}
    raw = meta.get("validation_requirements") if isinstance(meta, dict) else None
    if not isinstance(raw, list):
        return []
    return [
        str(item).strip() for item in raw if _REQUIREMENT_RE.match(str(item).strip())
    ]


def classify_inconclusive(
    exit_code: Any, output: str = "", own_modules: set[str] | None = None
) -> str | None:
    """Why this check settled nothing, or ``None`` when it produced a verdict.

    Two families. The exit codes in :data:`INCONCLUSIVE_EXIT_CODES` say it on
    their own — a timeout, a kill, a command that never launched, a command the
    shell could not find. The rest have to be read out of the output, because
    the failure is reported by something *inside* the command: make exits 2 when
    a recipe's binary is missing, and ``uv`` exits 2 when it cannot resolve an
    index host.

    Deliberately not here: ``No such file or directory``. That is usually the
    agent not having written the file, which is the agent's failure and the
    whole point of the check.
    """
    reason = _EXIT_CODE_REASONS.get(exit_code)
    if reason is not None:
        return reason
    if exit_code == 0 or exit_code is None:
        return None
    text = output or ""
    if _NOT_FOUND_RE.search(text):
        return "not_found"
    if _NO_NETWORK_RE.search(text):
        return "no_network"
    missing = _MISSING_MODULE_RE.search(text)
    if missing and missing.group(1) not in (own_modules or set()):
        return "missing_dependency"
    return None


def describe_inconclusive(checks: list[dict[str, Any]]) -> str:
    """``command not found, no network (2)`` — why nothing was measured.

    The old wording was a fixed list of every reason the lab knew, printed
    whenever any of them held. It read as a guess; a reader chasing a red gate
    needs the one that actually happened. A count is added only where it says
    something — one of a kind is already implied by naming it.
    """
    counts: dict[str, int] = {}
    for check in checks:
        if check.get("conclusive") is not False:
            continue
        reason = str(check.get("inconclusive_reason") or "") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return ", ".join(
        INCONCLUSIVE_LABELS.get(reason, reason) + (f" ({count})" if count > 1 else "")
        for reason, count in sorted(counts.items())
    )


if TYPE_CHECKING:
    from pathlib import Path

    from harness_lab_app.models import Case

# Case types whose objective validation is a "does it run" smoke check vs a
# "do the tests pass" suite. Used only to label the strategy.
_SMOKE_TYPES = {"build_from_scratch", "single_prompt", "multi_step"}


def collect_cost(run_dir: Path) -> dict[str, Any]:
    """Cost dimension from ``usage.json`` (tokens, money, wall time)."""
    usage = _read_json(run_dir / "usage.json")
    if not usage:
        return {"available": False}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    cache_read = usage.get("cache_read_tokens")
    cache_creation = usage.get("cache_creation_tokens")
    total = None
    if input_tokens is not None or output_tokens is not None:
        total = (input_tokens or 0) + (output_tokens or 0)
    # `total_tokens` keeps its historical meaning (ADR-0034: a name never changes
    # meaning). What a harness really costs is the prompt cache it re-reads on
    # every turn, so the billed figure is a separate number beside it.
    billed = None
    if total is not None or cache_read is not None or cache_creation is not None:
        billed = (total or 0) + (cache_read or 0) + (cache_creation or 0)
    return {
        "available": True,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "billed_tokens": billed,
        "cost_usd": usage.get("cost_usd"),
        "time_minutes": usage.get("time_minutes"),
    }


def collect_effort(
    run_dir: Path, interventions: int | None, continuations: int = 0
) -> dict[str, Any]:
    """Effort dimension: agent work, loop restarts and human interventions.

    ``continuations`` is the run's chain depth (ADR-0026). Each link is a human
    who read the workspace and wrote a new instruction — effort the run's own
    step count cannot see, because the earlier steps belong to another run.

    ``steps`` counts *restarts of the harness process*, not work: a run that
    solved the task in one invocation of fifty turns reports one step, and a
    run restarted ten times reports ten. ``turns`` and ``tool_calls`` are the
    work measures. Both are kept because the gap between them is exactly what
    a stop-criterion problem looks like, and ``stop_reason`` says which
    criterion ended the loop.
    """
    usage = _read_json(run_dir / "usage.json")
    steps = usage.get("steps") if usage else None
    if steps is None:
        steps_dir = run_dir / "steps"
        if steps_dir.is_dir():
            logs = list(steps_dir.glob("step-*.log"))
            steps = len(logs) or None
    return {
        "steps": steps,
        "turns": usage.get("turns") if usage else None,
        "tool_calls": usage.get("tool_calls") if usage else None,
        "stop_reason": usage.get("stop_reason") if usage else None,
        "interventions": interventions,
        "continuations": continuations,
    }


def build_validation_spec(
    case: Case, run_id: str, target_commands: list[str] | None = None
) -> dict[str, Any]:
    """Executable objective-validation spec for a case, or a manual marker.

    Case ``validation_commands`` are shell strings with ``{workspace}`` /
    ``{run_id}`` placeholders. Prose-only entries (no placeholder and not a
    runnable command) are treated as manual notes and skipped.

    ``target_commands`` are the target repository's own gates (ADR-0029) and
    **replace** the case's when present. A repository knows how it is tested
    better than a portable case does: a case that runs across several
    repositories cannot name each one's test command, and merging the two would
    run the case's guess *as well as* the repo's truth, failing runs for the
    wrong reason.

    A repository's gate is written relative to *its* root, and the lab's root is
    not it. `make test` from a target row carries no ``{workspace}`` to anchor —
    nor should it, since the same string has to work in a gate check — so it is
    prefixed with a ``cd`` into the workspace, exactly as
    :func:`gate_check_service.build_gate_check_spec` already does. Without it
    every target-paired run validated the *lab*: the eight runs of
    `audit-ml-python-base` all recorded `0/2 checks passed` from the lab's own
    Makefile, and the experiment read as "the harness did not help" (ADR-0062).
    Case commands are not prefixed: they anchor on ``{workspace}`` themselves,
    and a ``cd`` would break the lab-relative paths some of them also use.
    """
    # Managed (--fresh) runs archive + remove the live workspace, so prefer the
    # archived copy under data/runs/<id>/workspace when it exists (also the home
    # of a provisioned SWE-bench repo); fall back to the live path otherwise.
    archived = get_settings().data_runs_dir / run_id / "workspace"
    workspace = (
        f"data/runs/{run_id}/workspace" if archived.exists() else f"workspace/{run_id}"
    )
    in_workspace = bool(target_commands)
    commands: list[list[str]] = []
    for raw in target_commands or case.validation_commands or []:
        if not is_executable_command(raw):
            # The same predicate the case editor refuses on, so a command the
            # lab accepted is a command the lab runs. This used to be the
            # narrower `_looks_executable`, and the gap was silent: a stored
            # command it did not recognise disappeared without a check, a note
            # or a line in the log.
            continue
        # `.` for a target command: it already runs *inside* the workspace, and
        # the lab-relative path would resolve under itself after the cd.
        rendered = _portable_timeout(
            raw.format(workspace="." if in_workspace else workspace, run_id=run_id)
        )
        if in_workspace:
            rendered = f"cd {shlex.quote(workspace)} && {rendered}"
        # `-c` and not `-lc`: a login shell resets PATH and would drop the
        # runner image's /opt/venv/bin, so `python` would miss the installed
        # deps. Only argv[0] varies. `/bin/sh` in that image is dash, so a
        # command that uses bash syntax — `[[`, `<(`, `pipefail` — has to be
        # handed to bash; everything that ran under sh before still does.
        commands.append([shell_for_command(rendered), "-c", rendered])
    strategy = _strategy_for(case, commands)
    # Pro validation shells out to docker; the runner/validation container has
    # no socket, so those commands must execute where the socket lives (the
    # worker process / host) — flagged here, honored by ValidationService.
    local = case.type == CaseType.swe_pro_instance
    # The graders' own dependencies (ADR-0071). The checks run offline in a
    # clean container, so a case whose tests import pandas must have it
    # installed first, by a step that has network, into the run's own folder.
    # Never the workspace's requirements.txt: the agent wrote it.
    setup: list[list[str]] = []
    requirements = validation_requirements(case)
    if requirements and commands:
        deps = f"data/runs/{run_id}/{VALIDATION_DEPS_DIR}"
        # uv first: the runner's /opt/venv is built by uv and ships no pip.
        # pip remains the fallback for a host or image that has it.
        packages = " ".join(shlex.quote(item) for item in requirements)
        target = shlex.quote(deps)
        install = (
            f"rm -rf {target} && if command -v uv >/dev/null 2>&1; then "
            f"uv pip install --quiet --python python --target {target} {packages}; "
            f"else python -m pip install --quiet --disable-pip-version-check "
            f"--no-input --target {target} {packages}; fi"
        )
        setup.append(["sh", "-c", install])
        # Absolute, resolved before any `cd` the check makes into the workspace.
        commands = [
            [
                *argv[:-1],
                f'export PYTHONPATH="$PWD/{deps}${{PYTHONPATH:+:$PYTHONPATH}}"; {argv[-1]}',
            ]
            for argv in commands
        ]
    return {
        "strategy": strategy,
        "setup": setup,
        "commands": commands,
        "workspace": workspace,
        # Where the gate ran. Informational — the `cd` is already in the argv —
        # but a run whose checks all failed should be able to say from where.
        "cwd": workspace if in_workspace else None,
        "local": local,
    }


def parse_objective(results: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    """Assemble the objective dimension from per-command execution results.

    Each result is ``{"command": str, "exit_code": int, "output": str}``.

    A check is **conclusive** when it actually produced a verdict. One that
    timed out, was killed, never launched, could not find its command or had no
    network did not measure the agent's work, and ``passed`` distinguishes the
    three outcomes accordingly: ``False`` when something conclusively failed,
    ``None`` when nothing failed but something could not be measured, ``True``
    only when every check ran and passed. ``inconclusive_reason`` says which of
    those it was, so a red gate names its own cause (ADR-0062).
    """
    checks: list[dict[str, Any]] = []
    swebench_line: str | None = None
    for result in results:
        exit_code = result.get("exit_code")
        output = result.get("output", "")
        swebench_line = _swebench_summary(output) or swebench_line
        command = str(result.get("command", "") or "")
        reason = result.get("inconclusive_reason") or classify_inconclusive(
            exit_code, output, result.get("own_modules")
        )
        checks.append(
            {
                # `name` is what the UI and the audit have always read; the
                # command is the same string, under the key that says so.
                # Emitting both keeps old rows readable and stops a consumer
                # having to guess which one carries the text (it used to find
                # `command: None` and report a check it could not name).
                "name": command,
                "command": command,
                "passed": exit_code == 0,
                "conclusive": reason is None,
                "inconclusive_reason": reason,
                "exit_code": exit_code,
                "detail": _swebench_summary(output)
                or _pytest_summary(output)
                or _tail(output),
            }
        )
    if not checks:
        return {
            "strategy": strategy,
            "passed": None,
            "checks": [],
            "summary": "No objective validation defined (manual only).",
        }
    inconclusive = [check for check in checks if not check["conclusive"]]
    if any(not check["passed"] for check in checks if check["conclusive"]):
        # A real failure stands on its own; a sibling timeout does not excuse it.
        passed: bool | None = False
    elif inconclusive:
        passed = None
    else:
        passed = True
    # Prefer the SWE-bench F2P/P2P verdict as the headline when present.
    if swebench_line:
        summary = swebench_line
    elif len(inconclusive) == len(checks):
        # Every check settled nothing. "0/2 checks passed" is literally true and
        # reads as a verdict — it is the exact sentence this distinction exists
        # to stop, and it is what made eight unmeasured runs look like eight
        # failures on the page and to the auditor reading the same string.
        summary = (
            f"nothing measured: all {len(checks)} check(s) were inconclusive "
            f"({describe_inconclusive(checks)})"
        )
    else:
        summary = f"{sum(c['passed'] for c in checks)}/{len(checks)} checks passed"
        if inconclusive:
            summary += (
                f"; {len(inconclusive)} inconclusive ({describe_inconclusive(checks)})"
            )
    return {
        "strategy": strategy,
        "passed": passed,
        "checks": checks,
        "summary": summary,
    }


def describe_objective_for_judge(objective: dict[str, Any] | None) -> str:
    """Render the objective dimension as evidence an LLM judge can reason about.

    The judge used to receive only the headline — literally ``"0/1 checks
    passed"``. That is why all four runs of experiment `...-on--2` collapsed to
    1/10: a smoke command that timed out and an assertion that returned the
    wrong number reach the judge as the same eight words, so it cannot tell "the
    game does not work" from "we never found out". Give it the commands, their
    exit codes and their output tails instead, and say which ones settled
    nothing.
    """
    objective = objective or {}
    checks = objective.get("checks") or []
    summary = str(objective.get("summary") or "").strip()
    if not checks:
        return summary
    lines = [summary] if summary else []
    for check in checks:
        if check.get("conclusive") is False:
            reason = str(check.get("inconclusive_reason") or "")
            label = INCONCLUSIVE_LABELS.get(reason, reason)
            verdict = f"INCONCLUSIVE: {label}" if label else "INCONCLUSIVE"
        else:
            verdict = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- [{verdict}] exit={check.get('exit_code')} {check.get('name')}")
        detail = str(check.get("detail") or "").strip()
        if detail:
            lines.append(f"      {detail[:_JUDGE_DETAIL_CLIP]}")
    if any(check.get("conclusive") is False for check in checks):
        why = describe_inconclusive(checks)
        lines.append(
            f"An INCONCLUSIVE check settled nothing ({why}). It "
            "is evidence about the harness, not about the agent's code: it "
            "neither confirms nor refutes any rubric level."
        )
    return "\n".join(lines)


_JUDGE_DETAIL_CLIP = 600


def compute_composite(
    objective: dict[str, Any], subjective: dict[str, Any]
) -> float | None:
    """Explicit roll-up: functional score gated by the objective floor.

    - If a functional score (0-10) is present, start from it.
    - If objective validation ran and failed, cap the composite at 1.0 (the
      code does not run — level_1 in the Tetris rubric), because a high manual
      score is not credible when the smoke/tests fail.
    - If no functional score yet, the composite is undefined (None).
    """
    functional = subjective.get("functional_score")
    if functional is None:
        return None
    composite = float(max(0, min(10, functional)))
    if objective.get("passed") is False:
        composite = min(composite, 1.0)
    return round(composite, 1)


def _strategy_for(case: Case, commands: list[list[str]]) -> str:
    if not commands:
        return "manual"
    return "smoke" if str(case.type) in _SMOKE_TYPES else "tests"


# Command names / shell operators that mark a validation entry as executable
# (as opposed to a prose validation note like "Run the game and observe...").
_EXEC_HINTS = {
    "python",
    "python3",
    "pytest",
    "sh",
    "bash",
    "make",
    "npm",
    "node",
    "uv",
    "timeout",
    "go",
    "cargo",
    "ruff",
    "mypy",
}


def is_executable_command(command: str) -> bool:
    """Whether a validation entry would run, or be skipped as a prose note.

    Public face of the heuristic ``build_validation_spec`` applies, so anything
    that produces validation commands ahead of a run (the AI case assistant,
    imports) can refuse an entry at the same line the runner would drop it.

    Two things decide it: the shape heuristic below, and the runtime table in
    :mod:`harness_lab_app.runner_capabilities`. The second was added because
    the first knows a fixed vocabulary that the capability table had already
    outgrown — ``git -C /app diff --quiet`` is a real check that tetris-v2 has
    shipped since it was written, and ``build_validation_spec`` was silently
    dropping it. A command whose leading binary names a runtime is a command,
    whether or not ``_EXEC_HINTS`` has heard of that binary; whether the runner
    *has* that runtime is a separate question, answered by the capability match.
    """
    return _looks_executable(command) or bool(declared_runtime_for_binary(command))


# Files that are never a program. A hint that names one in backticks is a
# reader's check ("does this SKILL.md exist"), and a validation entry that IS
# one would be handed to `sh -c` and exit 126 — a failure about the case, read
# as a failure of the agent. `{workspace}/skills/x/SKILL.md` was once classified
# runnable because the placeholder short-circuited every other question.
_DOCUMENT_SUFFIXES = (
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".lock",
    ".html",
    ".css",
)


def names_a_document(command: str) -> bool:
    """Whether a command is nothing but a path to a file that cannot run."""
    try:
        tokens = shlex.split(command.strip())
    except ValueError:
        return False
    return len(tokens) == 1 and tokens[0].lower().endswith(_DOCUMENT_SUFFIXES)


def _looks_executable(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
        return False
    if names_a_document(stripped):
        return False
    if "{workspace}" in stripped or "{run_id}" in stripped:
        return True
    if any(op in stripped for op in ("&&", "||", "|", ";")):
        return True
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return False
    if not tokens:
        return False
    first = tokens[0]
    # Env-assignment prefix (FOO=1 python ...), a known command, or a path.
    return (
        "=" in first or first in _EXEC_HINTS or first.startswith("./") or "/" in first
    )


def _swebench_summary(output: str) -> str | None:
    """Extract the `SWEBENCH: resolved=… F2P=… P2P=…` verdict line, if present."""
    for line in reversed(output.splitlines()):
        if line.strip().startswith("SWEBENCH:"):
            return line.strip()[len("SWEBENCH:") :].strip()
    return None


def _pytest_summary(output: str) -> str | None:
    for line in reversed(output.splitlines()):
        low = line.lower()
        if ("passed" in low or "failed" in low or "error" in low) and "=" in line:
            return line.strip("= ").strip()
    return None


def _tail(output: str, lines: int = 3) -> str:
    return "\n".join(output.splitlines()[-lines:]).strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
