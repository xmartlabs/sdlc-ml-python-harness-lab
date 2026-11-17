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


def test_a_group_design_removes_every_item_in_one_arm_judged_as_one_item():
    from harness_lab_app.experiment_service import removed_item, removed_skills

    design = prune.design(
        cases=["a", "b", "c"],
        items=["verify_changes", "brainstorm_quick"],
        model="claude-haiku-4-5",
        harness="claude",
        reps=3,
        group=True,
    )
    control, *treatments = design.conditions
    assert len(treatments) == 1
    item = removed_item(control.overrides, treatments[0].overrides)
    assert item == "skills:brainstorm_quick,verify_changes"
    assert removed_skills(item) == ["brainstorm_quick", "verify_changes"]
    assert design.experiment_id.endswith("-2group")


def test_a_group_design_passes_validation_as_a_single_factor_experiment():
    from harness_lab_app.experiment_service import validate_design

    design = prune.design(
        cases=["a", "b", "c"],
        items=["x", "y"],
        model="m",
        harness="claude",
        reps=2,
        group=True,
    )
    conditions = [
        {"label": c.label, "role": c.role, "overrides": c.overrides}
        for c in design.conditions
    ]
    warnings = validate_design(design.mode, conditions, in_family=True)
    assert not any("More than one harness factor" in w for w in warnings)


def test_all_removes_the_whole_catalog_in_one_arm(monkeypatch, capsys):
    monkeypatch.setattr(prune, "known_skill_names", lambda: {"a", "b", "c"})
    monkeypatch.setattr(prune, "init_db", lambda: None)

    captured = {}

    class _Service:
        def __init__(self, session, enqueue=None):
            captured["enqueue"] = enqueue

        def create_experiment(self, data):
            captured["data"] = data
            return type("E", (), {"experiment_id": data.experiment_id})(), []

    monkeypatch.setattr(prune, "ExperimentService", _Service)
    monkeypatch.setattr(prune, "Session", lambda _engine: _Ctx())
    monkeypatch.setattr(prune, "get_engine", lambda: None)
    code = prune.main(["--group", "--all", "--model", "m", "--cases", "x", "y", "z"])
    assert code == 0
    assert captured["enqueue"] is not None
    treatment = captured["data"].conditions[1].overrides
    assert treatment["skills_disabled"] == ["a", "b", "c"]


def test_all_without_group_is_refused():
    import pytest

    with pytest.raises(SystemExit):
        prune.main(["--all", "--model", "m", "--cases", "x"])


class _Ctx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def test_every_launching_script_hands_the_service_a_queue_publisher():
    """Without `enqueue`, launch persists runs that never start (silently)."""
    for name in ("prune", "ladder", "profile_experiment", "refine_skill"):
        source = (_SCRIPTS / f"{name}.py").read_text(encoding="utf-8")
        assert "ExperimentService(session, enqueue=enqueue_run)" in source, name
