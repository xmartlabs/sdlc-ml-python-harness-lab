"""scripts/prune.py designs a leave-one-out family the summary can read (ADR-0076)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import prune  # noqa: E402


def _design(items=("verify_changes", "brainstorm_quick")):
    return prune.design(
        cases=["a", "b", "c"],
        items=list(items),
        model="claude-haiku-4-5",
        harness="claude",
        reps=3,
    )


def test_every_treatment_removes_exactly_one_item_from_the_control():
    from harness_lab_app.experiment_service import removed_item

    design = _design()
    control = design.conditions[0]
    assert control.role == "control"
    removed = [
        removed_item(control.overrides, condition.overrides)
        for condition in design.conditions[1:]
    ]
    assert removed == ["skill:verify_changes", "skill:brainstorm_quick"]


def test_the_family_is_marked_as_pruning_and_is_one_harness_model_pair():
    from harness_lab_app.experiment_service import PRUNING_FAMILY_PREFIX

    design = _design()
    assert design.family_id.startswith(PRUNING_FAMILY_PREFIX + "claude-")
    other = prune.design(
        cases=["a"], items=["x"], model="qwen3", harness="opencode", reps=1
    )
    assert other.family_id != design.family_id


def test_the_design_passes_validation_as_a_single_factor_experiment():
    from harness_lab_app.experiment_service import validate_design

    design = _design()
    conditions = [
        {"label": c.label, "role": c.role, "overrides": c.overrides}
        for c in design.conditions
    ]
    warnings = validate_design(design.mode, conditions, in_family=True)
    assert not any("More than one harness factor" in w for w in warnings)
