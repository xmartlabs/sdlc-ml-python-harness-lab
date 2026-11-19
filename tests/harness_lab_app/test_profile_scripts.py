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


def _add_trial(**overrides):
    import add_skill_trial

    kwargs = {
        "skill": "x",
        "cases": ["a", "b", "c"],
        "model": "m",
        "reps": 3,
        "base_disabled": ["unused", "x"],
    }
    return add_skill_trial.design(**{**kwargs, **overrides})


def test_an_add_trial_turns_one_skill_on_and_asks_for_it():
    design = _add_trial()
    control, treatment = (c.overrides for c in design.conditions)
    assert control["skills_disabled"] == ["unused", "x"]
    assert control["skills_invoke"] == []
    assert treatment["skills_disabled"] == ["unused"]
    assert treatment["skills_invoke"] == ["x"]
    assert control["prompt_variant"] == treatment["prompt_variant"]
    assert "skills_extra" not in treatment


def test_an_add_trial_is_one_harness_factor_not_two():
    """Re-enabling a governed skill and invoking it is one lever (ADR-0089)."""
    warnings = validate_design(
        _add_trial().mode, _conditions(_add_trial()), in_family=True
    )
    assert not any("More than one harness factor" in w for w in warnings)


@pytest.mark.parametrize(
    "treatment",
    [
        # Re-enabled without being asked for: availability, a second factor.
        {"skills_disabled": ["unused"], "skills_invoke": []},
        # Re-enables x but asks for something else.
        {"skills_disabled": ["unused"], "skills_invoke": ["y"]},
        # Re-enables two skills, asks for one.
        {"skills_disabled": [], "skills_invoke": ["x"]},
    ],
)
def test_an_enable_and_invoke_that_disagree_still_warns(treatment):
    from harness_lab_app.models import ExperimentMode

    conditions = [
        {
            "label": "minimal",
            "role": "control",
            "overrides": {"skills_disabled": ["unused", "x"], "skills_invoke": []},
        },
        {"label": "t", "role": "treatment", "overrides": treatment},
    ]
    warnings = validate_design(
        ExperimentMode.harness_evaluation, conditions, in_family=True
    )
    if treatment["skills_invoke"]:
        assert any("More than one harness factor" in w for w in warnings)
    else:
        # One field moves: a plain ablation, no confound to report.
        assert not any("More than one harness factor" in w for w in warnings)


def test_adding_a_skill_the_profile_already_carries_is_refused():
    with pytest.raises(ValueError, match="already in profile"):
        _add_trial(base_disabled=["unused"])


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
