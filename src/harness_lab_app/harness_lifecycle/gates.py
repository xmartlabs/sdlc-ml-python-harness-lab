"""Declarative hard-gate and warning evaluation for the regression suite.

Hard gates block adoption; warnings only surface regressions. Every threshold
comes from the suite definition (§12) — nothing is hard-coded here. Operators for
proposal success criteria reuse the ``CriterionOperator`` vocabulary from
:mod:`.models`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_lab_app.harness_lifecycle.metrics import percent_increase

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from harness_lab_app.harness_lifecycle.metrics import AggregatedArm
    from harness_lab_app.harness_lifecycle.models import (
        SuccessCriterion,
        SuiteDefinition,
    )


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str
    severity: str = "hard"  # "hard" blocks adoption; "warning" is informational


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "lte": lambda a, b: a <= b,
    "gte": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "lt": lambda a, b: a < b,
    "gt": lambda a, b: a > b,
}


def evaluate_criterion(criterion: SuccessCriterion, metrics: dict) -> bool:
    if criterion.metric not in metrics:
        return False
    return _OPS[criterion.operator](metrics[criterion.metric], criterion.value)


def evaluate_success_criteria(
    criteria: list[SuccessCriterion], metrics: dict
) -> tuple[bool, list[str]]:
    failed = [c.metric for c in criteria if not evaluate_criterion(c, metrics)]
    return (not failed, failed)


def _is_canary(role: str) -> bool:
    return "swebench" not in role and "swe-bench" not in role


def evaluate_hard_gates(
    suite: SuiteDefinition,
    per_case_baseline: dict[str, AggregatedArm],
    per_case_candidate: dict[str, AggregatedArm],
    *,
    controlled_ok: bool,
    controlled_diffs: list[str] | None = None,
    context: dict[str, bool] | None = None,
    proposal_case_id: str | None = None,
    proposal_criteria: list[SuccessCriterion] | None = None,
    proposal_metrics: dict | None = None,
) -> list[GateResult]:
    """Evaluate every hard gate. All must pass for the candidate to be adoptable."""
    results: list[GateResult] = []
    context = context or {}

    # Context gates verified upstream (Phase 4 / reports) and passed in as booleans.
    context_labels = {
        "release_valid": "Tag, SHA, release manifest, and sync protocol are valid",
        "governance_only": "Candidate changes only declared governance paths",
        "checks_pass": "Candidate make check-sync and make check pass",
        "attestation_clean": "Public attestation passes secret/private-path scanning",
    }
    for key, label in context_labels.items():
        if key in context:
            results.append(GateResult(key, bool(context[key]), label))

    results.append(
        GateResult(
            "controlled_conditions",
            controlled_ok,
            "Harness is the only deliberate variable"
            if controlled_ok
            else f"controlled fields differ: {controlled_diffs or []}",
        )
    )

    for case in suite.cases:
        if not _is_canary(case.role):
            continue
        agg = per_case_candidate.get(case.id)
        if agg is None or agg.runs == 0:
            results.append(GateResult(f"canary:{case.id}", False, "no candidate runs"))
            continue
        passed = agg.pass_ratio >= suite.gates.canary_pass_ratio
        results.append(
            GateResult(
                f"canary:{case.id}", passed, f"passed {agg.pass_count}/{agg.runs}"
            )
        )

    for case in suite.cases:
        baseline = per_case_baseline.get(case.id)
        candidate = per_case_candidate.get(case.id)
        if baseline is None or candidate is None:
            continue
        passed = candidate.pass_ratio >= baseline.pass_ratio
        results.append(
            GateResult(
                f"no_regression:{case.id}",
                passed,
                f"baseline {baseline.pass_ratio:.2f} → candidate {candidate.pass_ratio:.2f}",
            )
        )

    worst_loop = max(
        (agg.max_repeated_command for agg in per_case_candidate.values()), default=0
    )
    results.append(
        GateResult(
            "no_pathological_loop",
            worst_loop < suite.gates.pathological_loop_threshold,
            f"max repeated command run {worst_loop} "
            f"(threshold {suite.gates.pathological_loop_threshold})",
        )
    )

    worst_interventions = max(
        (agg.max_interventions for agg in per_case_candidate.values()), default=0
    )
    results.append(
        GateResult(
            "no_intervention",
            worst_interventions <= suite.gates.max_interventions,
            f"max interventions {worst_interventions}",
        )
    )

    if proposal_case_id and proposal_criteria:
        ok, failed = evaluate_success_criteria(
            proposal_criteria, proposal_metrics or {}
        )
        results.append(
            GateResult(
                "proposal_success_criterion",
                ok,
                "criteria met" if ok else f"failed: {failed}",
            )
        )

    return results


def evaluate_warnings(
    suite: SuiteDefinition, baseline: AggregatedArm, candidate: AggregatedArm
) -> list[GateResult]:
    """Non-blocking warnings comparing pooled candidate vs baseline medians."""
    w = suite.warnings
    results: list[GateResult] = []

    def _warn(name: str, increase: float, threshold: float, label: str) -> None:
        results.append(
            GateResult(
                name,
                increase <= threshold,
                f"{label} +{increase:.1f}% (threshold {threshold}%)",
                severity="warning",
            )
        )

    _warn(
        "tokens",
        percent_increase(baseline.median_tokens, candidate.median_tokens),
        w.median_token_increase_pct,
        "median tokens",
    )
    _warn(
        "cost",
        percent_increase(baseline.median_cost, candidate.median_cost),
        w.median_cost_increase_pct,
        "median cost",
    )
    _warn(
        "duration",
        percent_increase(baseline.median_duration, candidate.median_duration),
        w.median_duration_increase_pct,
        "median duration",
    )
    _warn(
        "steps",
        percent_increase(baseline.median_steps, candidate.median_steps),
        w.median_steps_increase_pct,
        "median steps",
    )
    drop = baseline.median_composite - candidate.median_composite
    results.append(
        GateResult(
            "composite",
            drop <= w.composite_drop_max,
            f"composite drop {drop:.2f} (max {w.composite_drop_max})",
            severity="warning",
        )
    )
    return results


def all_hard_pass(results: list[GateResult]) -> bool:
    return all(r.passed for r in results if r.severity == "hard")
