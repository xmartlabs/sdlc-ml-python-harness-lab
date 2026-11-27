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


def test_selection_rejects_incompatible_rules_and_protects_official(tmp_path):
    from harness_lab_app.gameplay.recordings import compatible_condition

    base = {
        "environment_id": "tidal-ring",
        "environment_version": "1",
        "seed": 7,
        "tick_rate": 60,
        "ticks_per_action": 12,
        "max_ticks": 7200,
        "engine_sha256": "abc",
    }
    assert compatible_condition([base, dict(base)]) == base
    with pytest.raises(ValueError, match="same"):
        compatible_condition([base, {**base, "ticks_per_action": 30}])
    with pytest.raises(ValueError):
        compatible_condition([])


def test_human_trace_stops_without_inventing_zero_actions(tmp_path):
    from harness_lab_app.gameplay.contracts import (
        Action,
        EpisodeCancelled,
        EpisodeConfig,
        PolicyReply,
    )
    from harness_lab_app.gameplay.fixture import LineWorld
    from harness_lab_app.gameplay.runner import run_episode

    env = LineWorld()
    count = 0

    def decide(observation, timeout):
        nonlocal count
        count += 1
        if count > 1:
            raise EpisodeCancelled()
        return PolicyReply(
            action_json=Action(
                observation_id=observation.observation_id,
                controls={
                    name: bound.minimum for name, bound in observation.controls.items()
                },
            ).model_dump_json()
        )

    replay = run_episode(
        env,
        decide,
        EpisodeConfig(episode_id="human-short", seed=7, max_ticks=20),
        tmp_path / "short",
    )
    assert replay.result.status == "cancelled"
    assert replay.result.simulation_ticks == 2
    assert len([f for f in replay.frames if f.action]) == 1
