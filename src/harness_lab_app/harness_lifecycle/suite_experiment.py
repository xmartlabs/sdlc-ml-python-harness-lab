"""The regression suite as a producer of experiments, not a second runner.

``benchmarks/<suite>/suite.yaml`` already declares the canonical measurement
design: which cases are representative, and how many times each is worth
repeating. What it lacked was a way to *execute* that design through the same
machinery as every other comparison, so validating a harness release used a
parallel path with its own aggregation and its own idea of what counts as a
regression.

This module closes that gap in the only direction that is safe: the suite
contributes the design (which cases, repeated how often), and
:class:`~harness_lab_app.experiment_service.ExperimentService` does everything
else — design validation, arm expansion, run creation, scoring, the case matrix.
There is no second runner, and a suite run is a `harness_evaluation` experiment
like any other, so `fixed / broken / unchanged` comes out of the existing
comparison rather than a bespoke report.

The suite deliberately does **not** decide what the two arms *are*. It fixes
what is measured; the caller fixes the harness change being measured, and the
suite's ``controlled_fields`` are enforced against that choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harness_lab_app.models import ConditionRole, ExperimentMode
from harness_lab_app.schemas import ExperimentConditionCreate, ExperimentCreate

if TYPE_CHECKING:
    from harness_lab_app.harness_lifecycle.models import SuiteDefinition

#: Suite ``controlled_fields`` that name a run axis a condition could vary.
#: ``case`` and ``prompt`` are controlled by construction — every arm runs the
#: same cases and the same prompt variant — so they need no check.
_CONTROLLED_AXES = {
    "executor_model": "executor_model",
    "planner_model": "planner_model",
    "max_steps": "max_steps",
}


class SuiteExperimentError(ValueError):
    """The suite cannot be turned into the experiment that was asked for."""


@dataclass(frozen=True)
class SuiteArm:
    """One side of the suite comparison, as the caller defines it."""

    label: str
    overrides: dict[str, Any] = field(default_factory=dict)
    description: str = ""


def default_question(suite_id: str, control: SuiteArm, treatment: SuiteArm) -> str:
    return (
        f"Does the harness change from '{control.label}' to '{treatment.label}' "
        f"improve outcomes on the {suite_id} regression suite?"
    )


def experiment_from_suite(
    suite: SuiteDefinition,
    *,
    experiment_id: str,
    control: SuiteArm,
    treatment: SuiteArm,
    prompt_variant: str = "baseline",
    question: str = "",
    name: str = "",
    notes: str = "",
) -> ExperimentCreate:
    """Translate a suite plus a harness change into a declared experiment.

    Raises :class:`SuiteExperimentError` when the request would silently produce
    a weaker measurement than the suite promises — no cases, arms that vary a
    field the suite pins, or two arms that are actually the same arm.
    """
    if not suite.cases:
        raise SuiteExperimentError(
            f"Suite '{suite.suite_id}' declares no cases, so there is nothing to "
            "measure."
        )
    if control.label == treatment.label:
        raise SuiteExperimentError("The two arms must have different labels.")
    if control.overrides == treatment.overrides:
        raise SuiteExperimentError(
            "Both arms fix the same axis values, so the experiment would compare "
            "a condition with itself. Give the treatment the harness change you "
            "want to measure."
        )

    violated = sorted(
        name_
        for suite_field, name_ in _CONTROLLED_AXES.items()
        if suite_field in suite.comparison.controlled_fields
        and control.overrides.get(name_) != treatment.overrides.get(name_)
    )
    if violated:
        raise SuiteExperimentError(
            f"Suite '{suite.suite_id}' controls {', '.join(violated)}, but the "
            "arms give them different values. A difference between the arms "
            "could not be attributed to the harness."
        )

    case_ids = [case.id for case in suite.cases]
    repetitions = {case.id: max(1, case.repetitions) for case in suite.cases}

    return ExperimentCreate(
        experiment_id=experiment_id,
        name=name or f"{suite.suite_id}: {control.label} vs {treatment.label}",
        question=question or default_question(suite.suite_id, control, treatment),
        mode=ExperimentMode.harness_evaluation,
        case_ids=case_ids,
        prompt_variants=[prompt_variant],
        conditions=[
            ExperimentConditionCreate(
                label=control.label,
                role=ConditionRole.control,
                description=control.description
                or f"Suite baseline ({suite.comparison.baseline}).",
                overrides=dict(control.overrides),
            ),
            ExperimentConditionCreate(
                label=treatment.label,
                role=ConditionRole.treatment,
                description=treatment.description
                or f"Suite candidate ({suite.comparison.candidate}).",
                overrides=dict(treatment.overrides),
            ),
        ],
        # The floor, so a caller reading only `repetitions` is never told the
        # experiment is stronger than its weakest case.
        repetitions=min(repetitions.values()),
        case_repetitions=repetitions,
        suite_id=suite.suite_id,
        notes=notes,
    )
