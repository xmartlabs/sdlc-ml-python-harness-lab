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


def _portable_timeout(command: str) -> str:
    """Rewrite a `timeout <N>` prefix so it runs where GNU timeout is absent.

    Prefers `gtimeout` (brew coreutils); otherwise drops the wrapper — the smoke
    commands self-exit (e.g. tetris under BENCH_SMOKE), so the guard is optional.
    """
    if shutil.which("timeout"):
        return command
    if shutil.which("gtimeout"):
        return _TIMEOUT_RE.sub(lambda m: f"gtimeout {m.group(1)} ", command)
    return _TIMEOUT_RE.sub("", command)


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
    total = None
    if input_tokens is not None or output_tokens is not None:
        total = (input_tokens or 0) + (output_tokens or 0)
    return {
        "available": True,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "cost_usd": usage.get("cost_usd"),
        "time_minutes": usage.get("time_minutes"),
    }


def collect_effort(
    run_dir: Path, interventions: int | None, continuations: int = 0
) -> dict[str, Any]:
    """Effort dimension: agent steps and human interventions.

    ``continuations`` is the run's chain depth (ADR-0026). Each link is a human
    who read the workspace and wrote a new instruction — effort the run's own
    step count cannot see, because the earlier steps belong to another run.
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
        "interventions": interventions,
        "continuations": continuations,
    }


def build_validation_spec(case: Case, run_id: str) -> dict[str, Any]:
    """Executable objective-validation spec for a case, or a manual marker.

    Case ``validation_commands`` are shell strings with ``{workspace}`` /
    ``{run_id}`` placeholders. Prose-only entries (no placeholder and not a
    runnable command) are treated as manual notes and skipped.
    """
    # Managed (--fresh) runs archive + remove the live workspace, so prefer the
    # archived copy under data/runs/<id>/workspace when it exists (also the home
    # of a provisioned SWE-bench repo); fall back to the live path otherwise.
    archived = get_settings().data_runs_dir / run_id / "workspace"
    workspace = (
        f"data/runs/{run_id}/workspace" if archived.exists() else f"workspace/{run_id}"
    )
    commands: list[list[str]] = []
    for raw in case.validation_commands or []:
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
    passed = all(check["passed"] for check in checks)
    # Prefer the SWE-bench F2P/P2P verdict as the headline when present.
    summary = (
        swebench_line
        or f"{sum(c['passed'] for c in checks)}/{len(checks)} checks passed"
    )
    return {
        "strategy": strategy,
        "passed": passed,
        "checks": checks,
        "summary": summary,
    }


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
