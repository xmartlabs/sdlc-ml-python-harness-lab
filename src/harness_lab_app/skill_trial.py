"""Additive skill trial: does one catalog skill earn a place in a profile? (ADR-0089).

Pruning (ADR-0076, ADR-0085) asks what can leave the harness; this asks the
opposite question, the one a small default profile needs: what should join it.
One ``harness_evaluation`` experiment:

    control    the base profile (``minimal`` by default), as a project carries it
    treatment  the same profile plus one governed skill, asked for (Enforce)

Asking for the skill measures "does it help when used", not "does the agent
find it" (ADR-0041): a skill that is never opened cannot win or lose.

The design lives here so the CLI (``scripts/add_skill_trial.py``) and the web
UI build exactly the same experiment: same arms, same overrides, same family.
"""

from __future__ import annotations

import re

from harness_lab_app.models import ConditionRole, ExperimentMode
from harness_lab_app.schemas import ExperimentConditionCreate, ExperimentCreate

HARNESS_PROMPT = "harness"
DEFAULT_PROFILE = "minimal"


def slug(text: str) -> str:
    """Lowercase, with every character outside ``[a-z0-9-]`` turned into ``-``.

    The original rule mapped only ``/``, ``.`` and ``_``; ids built from those
    characters come out identical. Anything else (``qwen3:8b`` from Ollama)
    used to fail the experiment-id slug check with a 422.
    """
    return re.sub(r"[^a-z0-9-]", "-", text.lower())


def trial_experiment_id(skill: str, profile: str, model: str) -> str:
    return f"add-{slug(skill)}-to-{slug(profile)}-{slug(model)}"[:64]


def design(
    *,
    skill: str,
    cases: list[str],
    model: str,
    reps: int,
    base_disabled: list[str],
    profile: str = DEFAULT_PROFILE,
    experiment_id: str | None = None,
    pin_arm: dict[str, str] | None = None,
) -> ExperimentCreate:
    """Control is ``profile``; treatment is ``profile`` + ``skill``, invoked.

    ``experiment_id`` defaults to the deterministic id the CLI has always used;
    the web UI passes a suffixed one so a second trial of the same skill does
    not collide with the first.

    ``pin_arm`` fixes settings in *both* arms (the web UI pins ``executor_model``
    and ``harness``): the experiment launches from a shared form whose defaults
    are someone else's, and an arm that does not name its model would run
    whatever that form held. Both arms get the same values, so the design still
    varies one harness factor. The CLI passes them at launch instead.
    """
    if skill not in base_disabled:
        raise ValueError(
            f"'{skill}' is already in profile '{profile}': adding it changes "
            "nothing. Use `make prune` to ask whether it can leave."
        )
    shared = dict(pin_arm or {})
    control = {
        **shared,
        "prompt_variant": HARNESS_PROMPT,
        "skills_disabled": sorted(base_disabled),
        "skills_invoke": [],
    }
    treatment = {
        **shared,
        "prompt_variant": HARNESS_PROMPT,
        "skills_disabled": sorted(set(base_disabled) - {skill}),
        "skills_invoke": [skill],
    }
    return ExperimentCreate(
        experiment_id=experiment_id or trial_experiment_id(skill, profile, model),
        name=f"Add {skill} to {profile} ({model})",
        question=(
            f"Does {profile} plus {skill}, when used, resolve more of these cases, "
            f"or as many sooner or cheaper, than {profile} alone?"
        ),
        mode=ExperimentMode.harness_evaluation,
        case_ids=list(cases),
        prompt_variants=[],
        repetitions=reps,
        family_id=f"add-to-{slug(profile)}"[:48],
        notes=(
            f"Additive skill trial (ADR-0089) on profile '{profile}'. The "
            "treatment re-enables one governed skill and asks for it — one "
            "intervention across two fields. Read skill_invoked first. "
            "Promotion is manual and needs a win on the target cases without a "
            "loss on the general one."
        ),
        conditions=[
            ExperimentConditionCreate(
                label=slug(profile)[:64],
                role=ConditionRole.control,
                overrides=control,
            ),
            ExperimentConditionCreate(
                label=f"{slug(profile)}-plus-{slug(skill)}"[:64],
                role=ConditionRole.treatment,
                overrides=treatment,
            ),
        ],
    )


CANDIDATE_FAMILY = "candidate-skill-trials"


def staged_trial_experiment_id(skill: str, model: str) -> str:
    return f"trial-{slug(skill)}-{slug(model)}"[:64]


def staged_design(
    *,
    skill: str,
    cases: list[str],
    model: str,
    reps: int,
    experiment_id: str | None = None,
    pin_arm: dict[str, str] | None = None,
) -> ExperimentCreate:
    """A staged skill — a case's candidate, or one you bring — against no skill.

    The arms are the ones **New evaluation → Add skills** builds with **Enforce**
    on, and the ones the first candidate trials used (family
    ``candidate-skill-trials``, ADR-0087): control adds nothing, treatment adds
    the staged skill and asks for it. The harness the run carries is the same in
    both, so the skill is the only difference (ADR-0091).
    """
    shared = dict(pin_arm or {})
    shared.setdefault("prompt_variant", HARNESS_PROMPT)
    control = {**shared, "skills_extra": [], "skills_invoke": []}
    treatment = {**shared, "skills_extra": [skill], "skills_invoke": [skill]}
    return ExperimentCreate(
        experiment_id=experiment_id or staged_trial_experiment_id(skill, model),
        name=f"Trial {skill} ({model})",
        question=(
            f"Does {skill}, when used, resolve more of these cases, or as many "
            "sooner or cheaper, than the same harness without it?"
        ),
        mode=ExperimentMode.harness_evaluation,
        case_ids=list(cases),
        prompt_variants=[],
        repetitions=reps,
        family_id=CANDIDATE_FAMILY,
        notes=(
            f"Staged-skill trial (ADR-0087, ADR-0091) of '{skill}'. Treatment "
            "adds the staged skill and asks for it (Enforce); control adds "
            "nothing. Read skill_invoked first, then the case's own checks. "
            "Promotion is manual (Skills → Promote) and needs a win."
        ),
        conditions=[
            ExperimentConditionCreate(
                label="control",
                role=ConditionRole.control,
                overrides=control,
            ),
            ExperimentConditionCreate(
                label=f"{slug(skill)}-enforced"[:64],
                role=ConditionRole.treatment,
                overrides=treatment,
            ),
        ],
    )


BASES = ("minimal", "full")


def base_family(skill: str, model: str) -> str:
    """One family per skill and model, so its minimal and full A/Bs read together."""
    return f"skill-{slug(skill)}-{slug(model)}"[:48]


def base_experiment_id(skill: str, base: str, model: str) -> str:
    return f"skill-{slug(skill)}-on-{base}-{slug(model)}"[:64]


def base_design(
    *,
    skill: str,
    staged: bool,
    base: str,
    cases: list[str],
    model: str,
    reps: int,
    experiment_id: str | None = None,
    pin_arm: dict[str, str] | None = None,
) -> ExperimentCreate:
    """``base`` against ``base`` + ``skill``, asked for (ADR-0092).

    ``base`` is a profile: ``minimal`` hides every skill outside it, ``full``
    hides nothing. A **staged** skill (a case's candidate, or one you brought)
    is added with ``skills_extra``. A **catalog** skill is already in ``full``,
    so it can only be trialled on a base that hides it: it is re-enabled by
    dropping it from ``skills_disabled``. Either way the two arms differ in the
    skill only — one harness factor, the condition ADR-0041 asks for.
    """
    from harness_lab_app.skills_catalog import profile_disabled

    if base not in BASES:
        raise ValueError(f"Unknown base '{base}'. Use one of: {', '.join(BASES)}.")
    disabled = sorted(profile_disabled(base))
    shared = dict(pin_arm or {})
    shared.setdefault("prompt_variant", HARNESS_PROMPT)
    if staged:
        control = {
            **shared,
            "skills_disabled": disabled,
            "skills_extra": [],
            "skills_invoke": [],
        }
        treatment = {
            **shared,
            "skills_disabled": disabled,
            "skills_extra": [skill],
            "skills_invoke": [skill],
        }
    else:
        if skill not in disabled:
            raise ValueError(
                f"'{skill}' is already in '{base}': adding it changes nothing. "
                "Trial a catalog skill on a base that hides it (minimal), or use "
                "`make prune` to ask whether it can leave full."
            )
        control = {
            **shared,
            "skills_disabled": disabled,
            "skills_extra": [],
            "skills_invoke": [],
        }
        treatment = {
            **shared,
            "skills_disabled": sorted(set(disabled) - {skill}),
            "skills_extra": [],
            "skills_invoke": [skill],
        }
    return ExperimentCreate(
        experiment_id=experiment_id or base_experiment_id(skill, base, model),
        name=f"{skill} on {base} ({model})",
        question=(
            f"On top of {base}, does {skill}, when used, resolve more of these "
            "cases, or as many sooner or cheaper, than the same base without it?"
        ),
        mode=ExperimentMode.harness_evaluation,
        case_ids=list(cases),
        prompt_variants=[],
        repetitions=reps,
        family_id=base_family(skill, model),
        notes=(
            f"Skill trial on base '{base}' (ADR-0092). Control is the base; "
            "treatment adds the skill and asks for it. Read skill_invoked "
            "first, then the case's own checks, then the delta. The family "
            "holds the same skill on the other base, to read side by side."
        ),
        conditions=[
            ExperimentConditionCreate(
                label=slug(base)[:64], role=ConditionRole.control, overrides=control
            ),
            ExperimentConditionCreate(
                label=f"{slug(base)}-plus-{slug(skill)}"[:64],
                role=ConditionRole.treatment,
                overrides=treatment,
            ),
        ],
    )


def same_design(stored: list[dict], wanted: ExperimentCreate) -> bool:
    """True when stored conditions are the wanted arms (labels aside)."""
    want = sorted(
        (str(c.role.value if hasattr(c.role, "value") else c.role), c.overrides)
        for c in wanted.conditions
    )
    have = sorted((str(c["role"]), c["overrides"]) for c in stored)
    return [(r, sorted(o.items())) for r, o in want] == [
        (r, sorted(o.items())) for r, o in have
    ]
