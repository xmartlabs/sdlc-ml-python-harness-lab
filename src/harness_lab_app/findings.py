"""Findings, and the maturity rubric an assessment is scored against.

A run is evidence. A finding is a claim about what that evidence means, and it
is only worth making if it can be *tested* — which is why every finding carries
a recommendation, the intervention that would implement it, and a slot for the
experiment that settles the question:

    Audit → Finding → Hypothesis → Experiment → Evidence → Recommendation

The rubric below is scored by a human. That is a deliberate limit, not a missing
feature: a maturity number the lab computed from a handful of runs would carry
far more authority than the evidence behind it supports.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness_lab_app.models import FindingStatus


@dataclass(frozen=True)
class RubricDimension:
    key: str
    name: str
    question: str
    # What each level looks like, 1-5. Written as observable practice rather
    # than sentiment, so two assessors reading the same evidence land close.
    levels: tuple[str, str, str, str, str]


RUBRIC: tuple[RubricDimension, ...] = (
    RubricDimension(
        key="standardization",
        name="Standardization",
        question="Do agents meet the same governance surface across the codebase?",
        levels=(
            "Every developer configures their own agent; nothing is shared.",
            "A README-level convention exists; adherence is voluntary.",
            "A shared governance file is committed and mostly current.",
            "Governance is generated from one source into every tool's format.",
            "Governance is versioned, reviewed, and drift is detected automatically.",
        ),
    ),
    RubricDimension(
        key="governance",
        name="Governance",
        question="Are the rules an agent must follow explicit and enforceable?",
        levels=(
            "Rules live in people's heads.",
            "Rules are written but not referenced by agents.",
            "Agents are pointed at the rules; compliance is unmeasured.",
            "Compliance is reviewed on a sample of agent work.",
            "Violations are caught by automation before merge.",
        ),
    ),
    RubricDimension(
        key="observability",
        name="Observability",
        question="Can you reconstruct what an agent did, after the fact?",
        levels=(
            "Nothing is retained beyond the diff.",
            "Transcripts exist locally, per developer.",
            "Transcripts are retained centrally for some work.",
            "Runs are traced with cost and step data attached.",
            "Traces are queryable and routinely read when work goes wrong.",
        ),
    ),
    RubricDimension(
        key="evaluation",
        name="Evaluation",
        question="Do you measure whether a change to the setup helped?",
        levels=(
            "Changes are made on impression.",
            "Changes are spot-checked on one task.",
            "A fixed task set is re-run after a change.",
            "Changes are A/B'd with repetitions on a shared task set.",
            "Results are versioned, and regressions block adoption.",
        ),
    ),
    RubricDimension(
        key="isolation",
        name="Isolation",
        question="Where does agent-written code run before it is trusted?",
        levels=(
            "On developer machines with full credentials.",
            "On developer machines, with some manual care.",
            "In containers, ad hoc.",
            "In per-task sandboxes with scoped credentials.",
            "Sandboxes are reproducible and their contents are recorded.",
        ),
    ),
    RubricDimension(
        key="regression",
        name="Regression suite",
        question="Do past failures become permanent tests?",
        levels=(
            "Failures are fixed and forgotten.",
            "Notable failures are discussed.",
            "Some failures become tickets.",
            "Failures become cases in a re-runnable suite.",
            "The suite gates changes to the agent setup.",
        ),
    ),
    RubricDimension(
        key="continuous_improvement",
        name="Continuous improvement",
        question="Does what you learn change the setup?",
        levels=(
            "No feedback loop.",
            "Occasional ad-hoc adjustments.",
            "Learnings are written down somewhere.",
            "Learnings become changes to the governance surface.",
            "The loop is routine, measured, and its effect is re-verified.",
        ),
    ),
)

RUBRIC_BY_KEY = {dimension.key: dimension for dimension in RUBRIC}


def validate_assessment(dimensions: dict) -> list[str]:
    """Check an assessment against the rubric. Returns warnings; raises on error.

    The load-bearing rule: **a level above 2 needs evidence.** Anything higher is
    a claim that a practice is actually in place, and a claim about a client's
    maturity with nothing behind it is the failure mode this whole exercise
    exists to avoid.
    """
    warnings: list[str] = []
    unknown = sorted(set(dimensions) - set(RUBRIC_BY_KEY))
    if unknown:
        raise ValueError(
            f"Unknown rubric dimension(s): {', '.join(unknown)}. "
            f"Valid: {', '.join(RUBRIC_BY_KEY)}."
        )
    for key, entry in dimensions.items():
        level = entry.get("level")
        if not isinstance(level, int) or not 1 <= level <= 5:
            raise ValueError(
                f"Dimension '{key}' needs a level between 1 and 5, got {level!r}."
            )
        evidence = entry.get("evidence") or []
        if level > 2 and not evidence:
            raise ValueError(
                f"Dimension '{key}' is scored {level}/5 with no evidence. A level "
                "above 2 claims a practice is in place — link the runs or "
                "findings that show it."
            )
        if not (entry.get("rationale") or "").strip():
            warnings.append(
                f"Dimension '{key}' has no rationale; the score will be unreadable "
                "to anyone who was not in the room."
            )
    missing = sorted(set(RUBRIC_BY_KEY) - set(dimensions))
    if missing:
        warnings.append(
            f"Not scored: {', '.join(missing)}. An unscored dimension is shown as "
            "unknown, never as zero."
        )
    return warnings


# Statuses from which a finding can still become an experiment. A validated or
# refuted finding has had its question answered; re-testing it is a new finding.
TESTABLE_STATUSES = frozenset(
    {FindingStatus.open, FindingStatus.proposed, FindingStatus.dismissed}
)
