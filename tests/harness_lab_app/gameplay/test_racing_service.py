"""Stored race records are re-simulated; client-provided scores are never accepted."""

import shutil

import pytest

from harness_lab_app.gameplay.contracts import EpisodeConfig
from harness_lab_app.gameplay.racing import RacingEnvironment, baseline_policy
from harness_lab_app.gameplay.racing_service import RacingService
from harness_lab_app.gameplay.runner import run_episode

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("node"), reason="Node required"),
]


def test_human_finish_is_resimulated_and_persisted(session, tmp_path):
    with RacingEnvironment() as env:
        reference = run_episode(
            env,
            baseline_policy(env),
            EpisodeConfig(
                episode_id="calibration",
                seed=7,
                tick_rate=60,
                ticks_per_action=12,
                max_ticks=7200,
            ),
            tmp_path / "reference",
        )
    controls = [f.action.controls for f in reference.frames]
    service = RacingService(session, tmp_path / "runs")
    row = service.save_human(7, controls)
    assert row.state == "completed"
    assert row.result_json["metrics"]["finished"] == 1
    assert row.result_json["inference_seconds"] == 0
    assert service.replay(row.episode_id).result.metrics == reference.result.metrics
    assert service.list_episodes()[0].episode_id == row.episode_id


def test_reject_oversized_recording_and_invalid_controls(session, tmp_path):
    service = RacingService(session, tmp_path / "runs")
    with pytest.raises(ValueError):
        service.save_human(7, [{"throttle": 999.0}])
    with pytest.raises(ValueError):
        service.save_human(7, [{}] * 601)
    assert service.list_episodes() == []


def test_failed_queue_publication_is_visible(session, tmp_path, monkeypatch):
    import harness_lab_app.gameplay.racing_service as module

    monkeypatch.setattr(
        module.LMStudioPolicy,
        "connect",
        lambda *_args: type("Policy", (), {"provenance": {"instance_id": "model-1"}})(),
    )

    def broken(episode_id):
        raise ConnectionError("private broker details")

    monkeypatch.setattr(module, "enqueue_race", broken)
    row = RacingService(session, tmp_path / "runs").launch_model(7, "installed/model")
    assert row.state == "failed"
    assert (
        row.failure_reason
        == "Could not queue model episode; check the worker and broker."
    )


def test_foreign_id_and_missing_replay_are_not_exposed(session, tmp_path):
    service = RacingService(session, tmp_path / "runs")
    with pytest.raises(LookupError):
        service.replay("../other")


def test_orphaned_running_episode_is_reported_as_failed(session, tmp_path):
    from datetime import timedelta

    from harness_lab_app.models import GameplayEpisode, utc_now

    row = GameplayEpisode(
        episode_id="race-" + "a" * 20,
        seed=7,
        controller="model",
        state="running",
        updated_at=utc_now() - timedelta(seconds=700),
    )
    session.add(row)
    session.commit()
    result = RacingService(session, tmp_path).list_episodes()[0]
    assert result.state == "failed"
    assert result.cancel_requested


def test_cancel_during_final_provider_reply_cannot_be_overwritten(
    session, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    import harness_lab_app.gameplay.racing_service as module
    from harness_lab_app.gameplay.contracts import EpisodeResult
    from harness_lab_app.models import GameplayEpisode

    service = RacingService(session, tmp_path)
    row = GameplayEpisode(
        episode_id="race-" + "b" * 20,
        seed=7,
        controller="model",
        model="test",
        policy_json={"instance_id": "test"},
    )
    session.add(row)
    session.commit()
    monkeypatch.setattr(
        module.LMStudioPolicy,
        "connect",
        lambda *_args: SimpleNamespace(instance_id="test", provenance={}),
    )

    def final_reply(*_args, **_kwargs):
        service.cancel(row.episode_id)
        return SimpleNamespace(
            result=EpisodeResult(
                status="provider_error",
                simulation_ticks=0,
                simulation_seconds=0.0,
                inference_seconds=0.0,
                latency_p50_seconds=0.0,
                latency_p95_seconds=0.0,
                metrics={},
            )
        )

    monkeypatch.setattr(module, "run_episode", final_reply)
    service.execute(row.episode_id)
    assert service.get(row.episode_id).state == "cancelled"


def test_scripted_ghost_is_verified_and_linked_to_human_recording(session, tmp_path):
    service = RacingService(session, tmp_path / "runs")
    row = service.create_calibration()
    assert row.controller == "calibration"
    source = service.ghost_reference(row.episode_id, 7)
    assert source["label"] == "SCRIPTED calibration"
    assert source["partial"] is False
    controls = [
        frame.action.controls for frame in service.replay(row.episode_id).frames
    ]
    human = service.save_human(7, controls, ghost_episode_id=row.episode_id)
    assert human.state == "completed"
    assert human.policy_json["ghost_episode_id"] == row.episode_id
    assert (
        service.replay(human.episode_id).policy_provenance["ghost_episode_id"]
        == row.episode_id
    )
    with pytest.raises(ValueError, match="seed"):
        service.ghost_reference(row.episode_id, 8)
    with pytest.raises(ValueError, match="model or scripted"):
        service.ghost_reference(human.episode_id, 7)


@pytest.mark.parametrize("corrupt_state", [True, False])
def test_ghost_rejects_missing_fingerprint_or_corrupt_recording(
    session, tmp_path, corrupt_state
):
    import json

    service = RacingService(session, tmp_path / "runs")
    row = service.create_calibration()
    path = tmp_path / "runs" / row.episode_id / "gameplay" / "replay.json"
    data = json.loads(path.read_text())
    if corrupt_state:
        data["frames"][20]["observation"]["state"]["x"] += 1
    else:
        data["environment_provenance"] = {}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        service.ghost_reference(row.episode_id, 7)


@pytest.mark.parametrize("stop_tick,eligible", [(180, False), (360, True)])
def test_failed_model_ghost_requires_meaningful_coverage(
    session, tmp_path, stop_tick, eligible
):
    service = RacingService(session, tmp_path / "runs")
    row = service._new(7, "model", "recorded-test-model")
    service._save(row)
    with RacingEnvironment() as env:
        baseline = baseline_policy(env)

        def decide(observation, timeout):
            if observation.tick >= stop_tick:
                raise TimeoutError
            return baseline(observation, timeout)

        replay = run_episode(
            env,
            decide,
            service._config(row),
            tmp_path / "runs" / row.episode_id / "gameplay",
            clock=lambda: 0.0,
        )
    assert replay.result.status == "timeout"
    if eligible:
        reference = service.ghost_reference(row.episode_id, 7)
        assert reference["partial"] is True
        assert reference["finish_time_seconds"] is None
    else:
        with pytest.raises(ValueError, match="movement"):
            service.ghost_reference(row.episode_id, 7)
