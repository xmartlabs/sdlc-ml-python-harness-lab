"""Independent outcomes for the small gameplay instrument, not game benchmarks."""

import json

import pytest
from pydantic import ValidationError

from harness_lab_app.gameplay.contracts import Action, EpisodeConfig, PolicyReply
from harness_lab_app.gameplay.fixture import LineWorld
from harness_lab_app.gameplay.replay import verify_replay
from harness_lab_app.gameplay.runner import run_episode


def config(**overrides):
    return EpisodeConfig(**{"episode_id": "fixture-001", "seed": 7, **overrides})


def right(observation, timeout):
    return PolicyReply(
        action_json=json.dumps(
            {
                "version": "gameplay.v1",
                "observation_id": observation.observation_id,
                "controls": {"move": 1.0},
            }
        )
    )


def test_known_episode_and_persisted_replay_separate_inference_from_simulation(
    tmp_path,
):
    # Seed 7 puts the target at 4. Two decisions of two ticks each reach it.
    clock = iter([0.0, 0.25, 10.0, 10.75])
    replay = run_episode(
        LineWorld(), right, config(), tmp_path / "episode", clock=lambda: next(clock)
    )
    assert replay.result.status == "completed"
    assert replay.result.simulation_ticks == 4
    assert replay.result.simulation_seconds == 0.4
    assert replay.result.inference_seconds == 1.0
    assert replay.result.latency_p50_seconds == 0.5
    assert replay.result.latency_p95_seconds == 0.75
    assert replay.result.metrics == {"reached_goal": 1.0, "distance_remaining": 0.0}
    assert replay.frames[-1].observation.state["position"] == 4
    assert len((tmp_path / "episode" / "journal.jsonl").read_text().splitlines()) == 4
    loaded = verify_replay(tmp_path / "episode" / "replay.json", LineWorld())
    assert loaded == replay


@pytest.mark.parametrize(
    "reply",
    [
        '{"version":"gameplay.v1","observation_id":"stale","controls":{"move":1}}',
        '{"version":"gameplay.v1","observation_id":"ID","controls":{"move":2}}',
        '{"version":"gameplay.v1","observation_id":"ID","controls":{"teleport":1}}',
        '{"version":"gameplay.v1","observation_id":"ID","controls":{"move":true}}',
        '{"version":"gameplay.v1","observation_id":"ID","controls":{}}',
        '{"version":"gameplay.v2","observation_id":"ID","controls":{"move":1}}',
        "```json\n{}\n```",
    ],
)
def test_invalid_actions_end_episode_without_advancing_simulation(tmp_path, reply):
    def policy(observation, timeout):
        return PolicyReply(action_json=reply.replace("ID", observation.observation_id))

    replay = run_episode(LineWorld(), policy, config(), tmp_path / "episode")
    assert replay.result.status == "invalid_action"
    assert replay.result.simulation_ticks == 0
    assert replay.frames[0].action is None
    assert verify_replay(tmp_path / "episode" / "replay.json", LineWorld()) == replay


def test_deadline_exceeded_discards_late_action(tmp_path):
    clock = iter([1.0, 4.0])
    replay = run_episode(
        LineWorld(),
        right,
        config(decision_timeout_seconds=2.0),
        tmp_path / "episode",
        clock=lambda: next(clock),
    )
    assert replay.result.status == "timeout"
    assert replay.result.simulation_ticks == 0
    assert replay.result.inference_seconds == 3.0


def test_provider_failure_is_persisted_as_inconclusive_not_bad_play(tmp_path):
    def offline(observation, timeout):
        raise ConnectionError("private provider response")

    replay = run_episode(LineWorld(), offline, config(), tmp_path / "episode")
    assert replay.result.status == "provider_error"
    assert replay.result.metrics == {}
    assert (
        "private provider response"
        not in (tmp_path / "episode" / "replay.json").read_text()
    )


def test_budget_exhaustion_has_an_exact_tick_boundary(tmp_path):
    replay = run_episode(LineWorld(), right, config(max_ticks=3), tmp_path / "episode")
    assert replay.result.status == "budget_exhausted"
    assert replay.result.simulation_ticks == 3
    assert replay.result.metrics["distance_remaining"] == 1.0
    assert [f.ticks_advanced for f in replay.frames] == [2, 1]


def test_seed_and_actions_reproduce_state_despite_different_inference_latency(tmp_path):
    a = run_episode(LineWorld(), right, config(), tmp_path / "a")
    b = run_episode(LineWorld(), right, config(), tmp_path / "b")
    assert [f.observation for f in a.frames] == [f.observation for f in b.frames]
    other = run_episode(
        LineWorld(), right, EpisodeConfig(episode_id="other", seed=8), tmp_path / "c"
    )
    assert other.initial.state["goal"] != a.initial.state["goal"]


@pytest.mark.parametrize(
    "mutation", ["snapshot", "action", "truncated", "result", "initial"]
)
def test_replay_verifier_rejects_corruption(tmp_path, mutation):
    run_episode(LineWorld(), right, config(), tmp_path / "episode")
    path = tmp_path / "episode" / "replay.json"
    payload = json.loads(path.read_text())
    if mutation == "snapshot":
        payload["frames"][0]["observation"]["state"]["position"] = 999
    elif mutation == "action":
        payload["frames"][0]["action"]["controls"]["move"] = -1
    elif mutation == "truncated":
        payload["frames"].pop()
    elif mutation == "result":
        payload["result"]["metrics"]["reached_goal"] = 0
    else:
        payload["initial"]["state"]["goal"] = 100
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        verify_replay(path, LineWorld())


def test_recording_never_overwrites_an_existing_episode(tmp_path):
    target = tmp_path / "episode"
    run_episode(LineWorld(), right, config(), target)
    before = (target / "replay.json").read_bytes()
    with pytest.raises(FileExistsError):
        run_episode(LineWorld(), right, config(), target)
    assert (target / "replay.json").read_bytes() == before


def test_invalid_config_and_nonfinite_controls_are_rejected():
    with pytest.raises(ValidationError):
        config(ticks_per_action=0)
    with pytest.raises(ValidationError):
        config(seed=True)
    with pytest.raises(ValidationError):
        Action(observation_id="x", controls={"move": float("nan")})


def test_failed_final_write_keeps_incremental_journal(tmp_path, monkeypatch):
    import harness_lab_app.gameplay.replay as module

    def fail(source, destination):
        raise OSError("disk unavailable")

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        run_episode(LineWorld(), right, config(), tmp_path / "episode")
    assert (tmp_path / "episode" / "journal.jsonl").exists()
    assert not (tmp_path / "episode" / "replay.json").exists()
    assert not list((tmp_path / "episode").glob("*.tmp"))


def test_failed_provider_keeps_policy_identity_in_the_journal_and_replay(tmp_path):
    def offline(observation, timeout):
        raise TimeoutError("no response")

    replay = run_episode(
        LineWorld(),
        offline,
        config(),
        tmp_path / "episode",
        policy_provenance={"requested_model": "local/model", "quantization": "Q4"},
    )
    assert replay.result.status == "timeout"
    assert replay.policy_provenance["requested_model"] == "local/model"
    header = json.loads(
        (tmp_path / "episode" / "journal.jsonl").read_text().splitlines()[0]
    )
    assert header["payload"]["policy_provenance"]["quantization"] == "Q4"
