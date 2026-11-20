"""v1 against v2 of one skill, on the same cases and model (ADR-0085, ADR-0092).

Two shapes, one question — "is the rewrite better when used?":

* **governed** skill (in the catalog): control keeps it, treatment disables it
  and stages the candidate in its place. This is ``make refine-skill``; the
  script imports this design, so both build the same experiment.
* **staged** skill (a case's candidate, or one you brought): control adds v1,
  treatment adds v2. Neither is in the catalog, so nothing is disabled.

Both arms pin the same base, prompt, model and harness, so the version is the
only difference.
"""

from __future__ import annotations

from harness_lab_app.models import ConditionRole, ExperimentMode
from harness_lab_app.schemas import ExperimentConditionCreate, ExperimentCreate

HARNESS_PROMPT = "harness"
FULL = "full"


def design(
    *,
    skill: str,
    candidate: str,
    cases: list[str],
    model: str,
    reps: int,
    base_disabled: list[str],
    invoke: bool = False,
    profile: str = FULL,
) -> ExperimentCreate:
    """Control keeps governed ``skill``; treatment swaps it for staged ``candidate``.

    With ``invoke`` both arms are asked to use their version (ADR-0041), which
    measures "is the rewrite better when used" rather than "does it fire".
    """
    if skill in base_disabled:
        raise ValueError(
            f"'{skill}' is not in profile '{profile}': refine it under a profile "
            "that carries it"
        )
    control = {
        "prompt_variant": HARNESS_PROMPT,
        "skills_disabled": sorted(base_disabled),
        "skills_extra": [],
        "skills_invoke": [skill] if invoke else [],
    }
    treatment = {
        "prompt_variant": HARNESS_PROMPT,
        "skills_disabled": sorted({*base_disabled, skill}),
        "skills_extra": [candidate],
        "skills_invoke": [candidate] if invoke else [],
    }
    mode = "invoked" if invoke else "available"
    return ExperimentCreate(
        experiment_id=f"refine-{skill}-{candidate}-{mode}"[:64],
        name=f"Refine {skill}: {candidate} in its place ({model})",
        question=(
            f"Does {candidate} resolve as many cases as {skill}, sooner or "
            "cheaper, when it takes its place?"
        ),
        mode=ExperimentMode.harness_evaluation,
        case_ids=list(cases),
        prompt_variants=[],
        repetitions=reps,
        family_id=f"refine-{skill}"[:48],
        notes=(
            f"Skill refinement (ADR-0085) under profile '{profile}'. The "
            "treatment disables the governed skill and stages the candidate — "
            "one intervention across two fields. Promotion is manual."
        ),
        conditions=[
            ExperimentConditionCreate(
                label=f"governed-{skill}"[:64],
                role=ConditionRole.control,
                overrides=control,
            ),
            ExperimentConditionCreate(
                label=f"candidate-{candidate}"[:64],
                role=ConditionRole.treatment,
                overrides=treatment,
            ),
        ],
    )


def staged_design(
    *,
    skill: str,
    candidate: str,
    cases: list[str],
    model: str,
    reps: int,
    base_disabled: list[str],
    experiment_id: str,
    pin_arm: dict[str, str] | None = None,
) -> ExperimentCreate:
    """Control adds staged ``skill`` (v1); treatment adds staged ``candidate`` (v2).

    Both are asked for. ``base_disabled`` is the base profile, the same in both
    arms, so the version is the only difference.
    """
    shared = dict(pin_arm or {})
    shared.setdefault("prompt_variant", HARNESS_PROMPT)
    disabled = sorted(base_disabled)
    control = {
        **shared,
        "skills_disabled": disabled,
        "skills_extra": [skill],
        "skills_invoke": [skill],
    }
    treatment = {
        **shared,
        "skills_disabled": disabled,
        "skills_extra": [candidate],
        "skills_invoke": [candidate],
    }
    return ExperimentCreate(
        experiment_id=experiment_id,
        name=f"{skill} v1 vs {candidate} ({model})",
        question=(
            f"Used on the same cases, does {candidate} resolve more, or as many "
            f"sooner or cheaper, than {skill}?"
        ),
        mode=ExperimentMode.harness_evaluation,
        case_ids=list(cases),
        prompt_variants=[],
        repetitions=reps,
        family_id=f"refine-{skill}"[:48],
        notes=(
            "Staged-skill refinement (ADR-0092): v1 and v2 are both staged and "
            "both asked for; the base is the same in both arms. Read "
            "skill_invoked for each arm, then the case's checks, then the delta."
        ),
        conditions=[
            ExperimentConditionCreate(
                label=f"v1-{skill}"[:64],
                role=ConditionRole.control,
                overrides=control,
            ),
            ExperimentConditionCreate(
                label=f"v2-{candidate}"[:64],
                role=ConditionRole.treatment,
                overrides=treatment,
            ),
        ],
    )
