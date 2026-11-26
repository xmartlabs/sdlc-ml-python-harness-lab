"""A comparison freezes shared rules and keeps provider failures distinct."""

import json

import pytest

from harness_lab_app.gameplay.comparison import (
    CONDITION,
    ComparisonStore,
    condition_fingerprint,
    outcome_label,
)


def test_condition_has_one_frozen_practical_lap_budget():
    assert CONDITION["tick_rate"] == 60
    assert CONDITION["ticks_per_action"] == 30
    assert CONDITION["max_ticks"] == 3780
    assert CONDITION["wall_budget_seconds"] == 1800
    assert condition_fingerprint(CONDITION) != condition_fingerprint(
        {**CONDITION, "seed": 8}
    )


def test_missing_session_and_path_traversal(tmp_path):
    store = ComparisonStore(tmp_path)
    assert store.current()["competitors"] == []
    with pytest.raises(LookupError):
        store.replay("../private")


def test_atomic_manifest_and_explicit_outcomes(tmp_path):
    store = ComparisonStore(tmp_path)
    store.write({"condition": CONDITION, "competitors": [{"label": "test"}]})
    assert store.current()["competitors"][0]["label"] == "test"
    assert json.loads((tmp_path / "current.json").read_text())["condition"] == CONDITION
    assert outcome_label("completed", {"finished": 1}) == "finished"
    assert outcome_label("budget_exhausted", {"finished": 0}) == "DNF: simulation limit"
    assert outcome_label("timeout", {"finished": 0}) == "inconclusive: decision timeout"
    assert (
        outcome_label("provider_error", {"finished": 0}, "wall_limit")
        == "inconclusive: wall limit"
    )
