"""scripts/profile_experiment.py and scripts/refine_skill.py design valid experiments."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import profile_experiment  # noqa: E402
import refine_skill  # noqa: E402

from harness_lab_app.experiment_service import validate_design  # noqa: E402


def _conditions(design):
    return [
        {"label": c.label, "role": c.role, "overrides": c.overrides}
        for c in design.conditions
    ]


def test_the_profile_grid_is_a_full_harness_by_model_grid():
    design = profile_experiment.design(
        cases=["a", "b", "c"],
        models=["small-model", "large-model"],
        profiles={"full": [], "minimal": ["x", "y"]},
        reps=3,
    )
    assert len(design.conditions) == 4
    assert [c.role for c in design.conditions].count("control") == 1
    # Raises when the grid is incomplete or a second harness factor varies.
    validate_design(design.mode, _conditions(design), in_family=True)


def test_a_refinement_swaps_the_skill_and_nothing_else():
    design = refine_skill.design(
        skill="verify_changes",
        candidate="verify_changes-v2",
        cases=["a", "b", "c"],
        model="m",
        reps=3,
        base_disabled=["unused"],
    )
    control, treatment = (c.overrides for c in design.conditions)
    assert control["skills_disabled"] == ["unused"]
    assert treatment["skills_disabled"] == ["unused", "verify_changes"]
    assert treatment["skills_extra"] == ["verify_changes-v2"]
    assert control["prompt_variant"] == treatment["prompt_variant"]
    validate_design(design.mode, _conditions(design), in_family=True)


def test_an_invoked_refinement_asks_each_arm_for_its_own_version():
    design = refine_skill.design(
        skill="s",
        candidate="s2",
        cases=["a"],
        model="m",
        reps=1,
        base_disabled=[],
        invoke=True,
    )
    control, treatment = (c.overrides for c in design.conditions)
    assert control["skills_invoke"] == ["s"]
    assert treatment["skills_invoke"] == ["s2"]


def test_refining_a_skill_the_profile_does_not_carry_is_refused():
    with pytest.raises(ValueError, match="not in profile"):
        refine_skill.design(
            skill="s",
            candidate="s2",
            cases=["a"],
            model="m",
            reps=1,
            base_disabled=["s"],
            profile="minimal",
        )
