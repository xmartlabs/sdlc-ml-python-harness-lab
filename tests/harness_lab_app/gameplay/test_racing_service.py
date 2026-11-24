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
