"""Metric normalization and aggregation for the regression suite.

Turns a child run's raw artifacts (``usage.json`` + validation dimensions +
attribution) into a comparable :class:`RunMetrics`, aggregates repetitions by
median, detects pathological command loops, and checks that the controlled
conditions were held identical between the baseline and candidate arms. Pure
functions over plain dicts — no DB, no run execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WHITESPACE = re.compile(r"\s+")


# Canonical per-case metric vocabulary for proposal ``success_criteria``. A
# criterion whose ``metric`` is not one of these fails closed in
# ``gates.evaluate_criterion``, so the drafting LLM must draw only from this
# catalog. Whoever wires a proposal's ``proposal_metrics`` dict emits exactly
# these keys (all derived from ``RunMetrics`` / ``AggregatedArm`` below).
MEASURABLE_METRICS: dict[str, str] = {
    "objective_passed": "case objective met (bool)",
    "composite": "composite quality score (float)",
    "total_tokens": "total tokens used (int)",
    "cost_usd": "run cost in USD (float)",
    "duration_min": "wall-clock minutes (float)",
    "steps": "agent steps / turns (int)",
    "interventions": "manual interventions required (int)",
    "files_modified": "files changed (int)",
    "diff_lines": "lines changed (int)",
    "repeated_command_max": "longest run of equivalent consecutive commands (int)",
}


# --------------------------------------------------------------------------- #
# Per-run normalization
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunMetrics:
    case_id: str
    arm: str
    passed: bool
    composite: float | None
    total_tokens: int
    cost_usd: float
    duration_min: float
    steps: int
    interventions: int
    files_modified: int
    diff_lines: int
    repeated_command_max: int
    harness_hash: str | None = None


def normalize_command(command: str) -> str:
    """Collapse a command to a comparable form (lowercased, whitespace-folded)."""
    return _WHITESPACE.sub(" ", command.strip().lower())


def repeated_command_max(commands: list[str]) -> int:
    """Longest run of consecutive normalized-equivalent commands.

    ``["ls", "ls", "cat x"]`` → 2. Empty input → 0.
    """
    best = 0
    current = 0
    previous: str | None = None
    for command in commands:
        normalized = normalize_command(command)
        if normalized == previous:
            current += 1
        else:
            current = 1
            previous = normalized
        best = max(best, current)
    return best


def _tokens(usage: dict) -> int:
    total = usage.get("total_tokens")
    if total is not None:
        return int(total)
    return int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))


def _files_modified(usage: dict) -> int:
    value = usage.get("files_modified", 0)
    return len(value) if isinstance(value, list) else int(value)


def normalize_run(raw: dict) -> RunMetrics:
    """Build a :class:`RunMetrics` from a run's combined raw artifacts.

    Expected keys: ``case_id``, ``arm``, ``usage`` (usage.json), ``objective``
    (``{passed: bool}``), ``composite``, ``interventions``, ``commands`` (ordered
    command names), ``harness_hash``.
    """
    usage = raw.get("usage", {})
    objective = raw.get("objective", {})
    return RunMetrics(
        case_id=raw["case_id"],
        arm=raw["arm"],
        passed=bool(objective.get("passed", False)),
        composite=raw.get("composite"),
        total_tokens=_tokens(usage),
        cost_usd=float(usage.get("cost_usd", 0.0)),
        duration_min=float(usage.get("time_minutes", 0.0)),
        steps=int(usage.get("steps", 0)),
        interventions=int(raw.get("interventions", 0)),
        files_modified=_files_modified(usage),
        diff_lines=int(usage.get("diff_lines", 0)),
        repeated_command_max=repeated_command_max(list(raw.get("commands", []))),
        harness_hash=raw.get("harness_hash"),
    )


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def percent_increase(baseline: float, candidate: float) -> float:
    """Percent change baseline→candidate. 0 baseline → 0 (or inf if candidate>0)."""
    if baseline == 0:
        return 0.0 if candidate == 0 else float("inf")
    return (candidate - baseline) / baseline * 100.0


@dataclass(frozen=True)
class AggregatedArm:
    runs: int
    pass_count: int
    pass_ratio: float
    median_tokens: float
    median_cost: float
    median_duration: float
    median_steps: float
    median_composite: float
    max_repeated_command: int
    max_interventions: int


def aggregate(metrics: list[RunMetrics]) -> AggregatedArm:
    """Aggregate repetitions (of one case+arm, or a pool) by median / counts."""
    runs = len(metrics)
    pass_count = sum(1 for m in metrics if m.passed)
    composites = [m.composite for m in metrics if m.composite is not None]
    return AggregatedArm(
        runs=runs,
        pass_count=pass_count,
        pass_ratio=(pass_count / runs) if runs else 0.0,
        median_tokens=median([float(m.total_tokens) for m in metrics]),
        median_cost=median([m.cost_usd for m in metrics]),
        median_duration=median([m.duration_min for m in metrics]),
        median_steps=median([float(m.steps) for m in metrics]),
        median_composite=median([float(c) for c in composites]),
        max_repeated_command=max((m.repeated_command_max for m in metrics), default=0),
        max_interventions=max((m.interventions for m in metrics), default=0),
    )


def group_by_case(metrics: list[RunMetrics]) -> dict[str, AggregatedArm]:
    by_case: dict[str, list[RunMetrics]] = {}
    for m in metrics:
        by_case.setdefault(m.case_id, []).append(m)
    return {case_id: aggregate(items) for case_id, items in by_case.items()}


# --------------------------------------------------------------------------- #
# Controlled-condition isolation
# --------------------------------------------------------------------------- #


def controlled_match(baseline: dict, candidate: dict) -> tuple[bool, list[str]]:
    """True when every controlled field is identical across the two arms.

    Returns ``(ok, differing_keys)``. The harness is meant to be the *only*
    deliberate variable, so any differing controlled field fails the isolation gate.
    """
    keys = set(baseline) | set(candidate)
    differing = sorted(k for k in keys if baseline.get(k) != candidate.get(k))
    return (not differing, differing)
