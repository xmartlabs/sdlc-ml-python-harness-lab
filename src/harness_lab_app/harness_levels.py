"""How much harness does this model need on this case? A ladder of clean A/Bs.

Three levels — ``bare`` (no harness), ``minimal`` (the default profile) and
``full`` (the whole catalog) — chosen two or three at a time. One experiment
holding all three would move two harness factors at once (bare→minimal changes
governance *and* skills), which the design validator rightly flags.

So each adjacent pair of the chosen levels is its own ``harness_evaluation``
A/B — bare→minimal, minimal→full, or bare→full when minimal is skipped — in one
family, read side by side as the harness ladder (ADR-0051, ADR-0093). The model
and harness are pinned in every arm; only the level moves.
"""

from __future__ import annotations

from itertools import pairwise

from harness_lab_app.models import ConditionRole, ExperimentMode
from harness_lab_app.schemas import ExperimentConditionCreate, ExperimentCreate

LEVELS = ("bare", "minimal", "full")


def slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9-]", "-", text.lower())


def level_overrides(level: str, minimal_disabled: list[str]) -> dict:
    if level == "bare":
        return {"harness_source": "bare"}
    if level == "minimal":
        return {
            "harness_source": "lab_root",
            "skills_disabled": sorted(minimal_disabled),
        }
    if level == "full":
        return {"harness_source": "lab_root", "skills_disabled": []}
    raise ValueError(f"Unknown level '{level}'. Use: {', '.join(LEVELS)}.")


def ladder_family(case_id: str, model: str) -> str:
    return f"levels-{slug(case_id)}-{slug(model)}"[:48]


def rungs(levels: list[str]) -> list[tuple[str, str]]:
    """Adjacent pairs of the chosen levels, in ladder order."""
    chosen = [level for level in LEVELS if level in set(levels)]
    if len(chosen) < 2:
        raise ValueError("Pick at least two harness levels to compare.")
    return list(pairwise(chosen))


def ladder_designs(
    *,
    case_id: str,
    model: str,
    harness: str,
    levels: list[str],
    reps: int,
    minimal_disabled: list[str],
    prompt_variant: str = "default",
) -> list[ExperimentCreate]:
    """One clean A/B per rung, in one family. Never launched here."""
    pin = {
        "harness": harness,
        "executor_model": model,
        "prompt_variant": prompt_variant,
    }
    family = ladder_family(case_id, model)
    designs: list[ExperimentCreate] = []
    for lower, upper in rungs(levels):
        designs.append(
            ExperimentCreate(
                experiment_id=f"{family}-{lower}-to-{upper}"[:64],
                name=f"{case_id}: {lower} → {upper} ({model})",
                question=(
                    f"On {case_id} with {model}, does {upper} resolve more, or as "
                    f"many sooner or cheaper, than {lower}?"
                ),
                mode=ExperimentMode.harness_evaluation,
                case_ids=[case_id],
                prompt_variants=[],
                repetitions=reps,
                family_id=family,
                notes=(
                    f"Harness-level rung {lower} → {upper} (ADR-0093). Read with "
                    f"its siblings in family '{family}'; only the level moves."
                ),
                conditions=[
                    ExperimentConditionCreate(
                        label=lower,
                        role=ConditionRole.control,
                        overrides={**pin, **level_overrides(lower, minimal_disabled)},
                    ),
                    ExperimentConditionCreate(
                        label=upper,
                        role=ConditionRole.treatment,
                        overrides={**pin, **level_overrides(upper, minimal_disabled)},
                    ),
                ],
            )
        )
    return designs
