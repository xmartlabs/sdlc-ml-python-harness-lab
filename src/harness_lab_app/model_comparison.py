"""Comparing models on one case: one arm per model, every other setting shared.

The question a new frontier model raises is "how does it do on the case the
last one did?" — not whether a harness helps. The lab already runs that design:
a ``subject_validation`` experiment (ADR-0040) whose arms differ only by
harness and executor model, with no control arm, reported per arm and never
pooled. What it lacked was a reading suited to hard cases.

A frontier game case is rarely passed outright: chess3d-v1 has 40 checks and a
strong run stops near 31. Read as pass/fail, every model scores 0% and the
comparison says nothing. This module reads the same experiment as checks
passed per run, phase by phase, with cost and time — from the scores and the
per-step reward the runs already recorded. Nothing new is stored.

Every comparison on a case shares the family ``models-<case_id>``, so the
experiments page lists them together (ADR-0051) and a new model's comparison
sits next to the last one.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any

from sqlmodel import select

from harness_lab_app.models import Score, Validation

if TYPE_CHECKING:
    from harness_lab_app.experiment_service import ExperimentService

MODEL_FAMILY_PREFIX = "models-"


def model_family_id(case_id: str) -> str:
    """The family every model comparison on ``case_id`` is filed under."""
    return f"{MODEL_FAMILY_PREFIX}{case_id}"


def is_model_comparison(family_id: str | None) -> bool:
    """Whether a family is filed as a model comparison — a reading, not a gate."""
    return str(family_id or "").startswith(MODEL_FAMILY_PREFIX)


# What an arm of a model comparison may fix: the model, the harness that drives
# it, and its reasoning effort. Anything else is another design.
MODEL_ARM_FIELDS = frozenset({"harness", "executor_model", "executor_effort"})


def model_comparison_shape_errors(
    mode: str, conditions: list[tuple[str, str, dict[str, Any]]]
) -> list[str]:
    """Why an experiment is not a model comparison; empty when it is one.

    The ``models-`` family prefix is a name anyone can type, so it decides how
    the experiment is *read*, never what it may *do*. Two lanes at once
    (ADR-0082) are safe only for this shape: a ``subject_validation`` design
    whose arms are peers (no control) that fix a harness and a model and
    nothing else, so none of them rewrites the lab's shared tree.

    ``conditions`` is ``(label, role, overrides)`` per arm.
    """
    errors: list[str] = []
    if str(mode) != "subject_validation":
        errors.append(f"its mode is {mode}, not subject_validation")
    if len(conditions) < 2:
        errors.append("it has fewer than two arms")
    for label, role, overrides in conditions:
        if str(role) != "treatment":
            errors.append(f"arm '{label}' is a {role}, and model arms are peers")
        extra = sorted(set(overrides) - MODEL_ARM_FIELDS)
        if extra:
            errors.append(f"arm '{label}' also sets {', '.join(extra)}")
        if not overrides.get("harness") or not overrides.get("executor_model"):
            errors.append(f"arm '{label}' does not fix a harness and a model")
    return errors


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _checks(objective: dict[str, Any]) -> tuple[int | None, int | None]:
    """Final checks passed and counted, leaving out the ones that measured nothing."""
    checks = objective.get("checks")
    if not isinstance(checks, list) or not checks:
        return None, None
    conclusive = [
        c for c in checks if isinstance(c, dict) and c.get("conclusive") is not False
    ]
    return sum(1 for c in conclusive if c.get("passed")), len(conclusive)


def _phases(objective: dict[str, Any]) -> list[dict[str, Any]]:
    per_step = objective.get("per_step")
    rows = per_step.get("steps") if isinstance(per_step, dict) else None
    phases: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("step"), int):
            continue
        passed, total = row.get("passed"), row.get("total")
        measured = isinstance(passed, int) and isinstance(total, int)
        phases.append(
            {
                "step": row["step"],
                "passed": passed if measured else None,
                "total": total if measured else None,
            }
        )
    return phases


def model_comparison(service: ExperimentService, experiment_id: str) -> dict[str, Any]:
    """Each arm's harness, model and runs: checks, phases, cost and minutes."""
    from harness_lab_app.run_service import RunService

    experiment = service.get_experiment(experiment_id)
    session = service.session
    runs_service = RunService(session)
    by_label: dict[str, list[Any]] = {}
    for run in service.runs(experiment_id):
        by_label.setdefault(run.condition_label or "", []).append(run)

    arms: list[dict[str, Any]] = []
    # In the order the arms were declared — the order the models were picked —
    # not the control-first, alphabetical order of a harness comparison.
    for condition in sorted(
        service.conditions(experiment_id), key=lambda row: row.id or 0
    ):
        overrides = condition.overrides or {}
        rows: list[dict[str, Any]] = []
        for run in sorted(by_label.get(condition.label, []), key=lambda r: r.run_id):
            config = runs_service.get_run_config(run.run_config_id)
            if config.dry_run:
                continue
            scores = {
                score.name: (
                    score.value_bool
                    if score.value_bool is not None
                    else score.value_num
                )
                for score in session.exec(
                    select(Score).where(Score.run_id == run.run_id)
                )
            }
            validation = session.exec(
                select(Validation).where(Validation.run_id == run.run_id)
            ).first()
            objective = (validation.objective if validation else None) or {}
            passed, total = _checks(objective)
            rows.append(
                {
                    "run_id": run.run_id,
                    "state": str(run.state.value),
                    "passed": passed,
                    "total": total,
                    "pass_rate": _number(scores.get("test_pass_rate")),
                    "cost_usd": _number(scores.get("cost_usd")),
                    "minutes": _number(scores.get("wall_time_minutes")),
                    "phases": _phases(objective),
                }
            )
        rates = [row["pass_rate"] for row in rows if row["pass_rate"] is not None]
        arms.append(
            {
                "label": condition.label,
                "harness": str(overrides.get("harness") or ""),
                "executor_model": str(overrides.get("executor_model") or ""),
                "executor_effort": str(overrides.get("executor_effort") or ""),
                "runs": rows,
                "mean_pass_rate": statistics.fmean(rates) if rates else None,
                "best_pass_rate": max(rates) if rates else None,
            }
        )
    return {
        "experiment_id": experiment_id,
        "case_id": experiment.case_ids[0] if experiment.case_ids else "",
        "family_id": experiment.family_id,
        "arms": arms,
    }
