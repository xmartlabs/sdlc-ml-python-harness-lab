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
from typing import TYPE_CHECKING, Any

from harness_lab_app.config import get_settings
from harness_lab_app.models import CaseType

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
# and -1 is this process failing to launch the command at all. ADR-0046 kept
# infrastructure out of an experiment's verdict; these keep it out of a run's
# capability score, which is where it entered in experiment `...-on--2`.
INCONCLUSIVE_EXIT_CODES = frozenset({-1, 124, 137, 143})


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
    """
    # Managed (--fresh) runs archive + remove the live workspace, so prefer the
    # archived copy under data/runs/<id>/workspace when it exists (also the home
    # of a provisioned SWE-bench repo); fall back to the live path otherwise.
    archived = get_settings().data_runs_dir / run_id / "workspace"
    workspace = (
        f"data/runs/{run_id}/workspace" if archived.exists() else f"workspace/{run_id}"
    )
    commands: list[list[str]] = []
    for raw in target_commands or case.validation_commands or []:
        if not _looks_executable(raw):
            continue
        rendered = _portable_timeout(raw.format(workspace=workspace, run_id=run_id))
        # sh -c (not -lc): a login shell resets PATH and would drop the runner
        # image's /opt/venv/bin, so `python` would miss the installed deps.
        commands.append(["sh", "-c", rendered])
    strategy = _strategy_for(case, commands)
    # Pro validation shells out to docker; the runner/validation container has
    # no socket, so those commands must execute where the socket lives (the
    # worker process / host) — flagged here, honored by ValidationService.
    local = case.type == CaseType.swe_pro_instance
    return {
        "strategy": strategy,
        "commands": commands,
        "workspace": workspace,
        "local": local,
    }


def parse_objective(results: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    """Assemble the objective dimension from per-command execution results.

    Each result is ``{"command": str, "exit_code": int, "output": str}``.

    A check is **conclusive** when it actually produced a verdict. One that
    timed out, was killed, or never launched did not measure the agent's work,
    and ``passed`` distinguishes the three outcomes accordingly: ``False`` when
    something conclusively failed, ``None`` when nothing failed but something
    could not be measured, ``True`` only when every check ran and passed.
    """
    checks = []
    swebench_line: str | None = None
    for result in results:
        exit_code = result.get("exit_code")
        output = result.get("output", "")
        swebench_line = _swebench_summary(output) or swebench_line
        checks.append(
            {
                "name": result.get("command", ""),
                "passed": exit_code == 0,
                "conclusive": exit_code not in INCONCLUSIVE_EXIT_CODES,
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
    summary = (
        swebench_line
        or f"{sum(c['passed'] for c in checks)}/{len(checks)} checks passed"
    )
    if inconclusive:
        summary += (
            f"; {len(inconclusive)} inconclusive (timed out, killed, or never launched)"
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
            verdict = "INCONCLUSIVE"
        else:
            verdict = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- [{verdict}] exit={check.get('exit_code')} {check.get('name')}")
        detail = str(check.get("detail") or "").strip()
        if detail:
            lines.append(f"      {detail[:_JUDGE_DETAIL_CLIP]}")
    if any(check.get("conclusive") is False for check in checks):
        lines.append(
            "An INCONCLUSIVE check timed out, was killed, or never launched. It "
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
    """
    return _looks_executable(command)


def _looks_executable(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
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
