"""Cooking shares deterministic recording while keeping game identities isolated."""

import shutil

import pytest

from harness_lab_app.gameplay.contracts import Action, ControlBounds
from harness_lab_app.gameplay.racing_service import RacingService


def test_integer_control_rejects_fractional_targets():
    from harness_lab_app.gameplay.contracts import Observation

    observation = Observation(
        episode_id="test",
        observation_id="first",
        tick=0,
        ticks_to_advance=10,
        tick_rate=10,
        instructions="Pick a station",
        state={},
        terminal=False,
        controls={"target": ControlBounds(minimum=-1.0, maximum=5.0, integer=True)},
    )
    with pytest.raises(ValueError, match="integer"):
        Action(observation_id="first", controls={"target": 1.5}).validate_for(
            observation
        )


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("node"), reason="Node required")
def test_cooking_calibration_recording_ghost_and_game_isolation(session, tmp_path):
    from harness_lab_app.gameplay.racing_service import CookingService

    service = CookingService(session, tmp_path)
    calibration = service.create_calibration()
    assert calibration.state == "completed", calibration.failure_reason
    assert calibration.environment_id == "harbor-kitchen"
    replay = service.replay(calibration.episode_id)
    assert replay.config.tick_rate == 10
    assert replay.config.ticks_per_action == 10
    assert replay.config.max_ticks == 930
    assert replay.result.metrics["orders_fulfilled"] > 0
    assert service.create_calibration().episode_id == calibration.episode_id
    ghost = service.ghost_reference(calibration.episode_id, 7)
    assert ghost["partial"] is False
    human = service.save_human(
        7, [frame.action.controls for frame in replay.frames], calibration.episode_id
    )
    assert human.state == "completed", human.failure_reason
    assert service.replay(human.episode_id).result.metrics == replay.result.metrics
    assert RacingService(session, tmp_path).list_episodes() == []
    with pytest.raises(LookupError):
        RacingService(session, tmp_path).get(human.episode_id)
    with pytest.raises(ValueError, match="seed"):
        service.ghost_reference(calibration.episode_id, 8)


def test_cooking_human_input_rejected_before_persistence(session, tmp_path):
    from harness_lab_app.gameplay.racing_service import CookingService

    service = CookingService(session, tmp_path)
    for controls in (
        {"target": 1.5, "interact": 1.0},
        {"target": 6.0, "interact": 1.0},
        {"target": True, "interact": 0.0},
        {"target": 0.0, "interact": float("nan")},
    ):
        with pytest.raises(ValueError):
            service.save_human(7, [controls])
    assert service.list_episodes() == []


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("node"), reason="Node required")
@pytest.mark.parametrize("stop_tick,eligible", [(30, False), (50, True)])
def test_partial_cooking_model_ghost_requires_post_countdown_movement(
    session, tmp_path, stop_tick, eligible
):
    from harness_lab_app.gameplay.cooking import CookingEnvironment
    from harness_lab_app.gameplay.racing import baseline_policy
    from harness_lab_app.gameplay.racing_service import CookingService
    from harness_lab_app.gameplay.runner import run_episode

    service = CookingService(session, tmp_path)
    row = service._save(service._new(7, "model", "recorded-model"))
    with CookingEnvironment() as env:
        baseline = baseline_policy(env)

        def decide(observation, timeout):
            if observation.tick >= stop_tick:
                raise TimeoutError
            return baseline(observation, timeout)

        replay = run_episode(
            env,
            decide,
            service._config(row),
            tmp_path / row.episode_id / "gameplay",
            clock=lambda: 0.0,
        )
    assert replay.result.status == "timeout"
    if eligible:
        ghost = service.ghost_reference(row.episode_id, 7)
        assert ghost["partial"] is True
        assert ghost["recording"]["result"]["metrics"] == {}
    else:
        with pytest.raises(ValueError, match="movement"):
            service.ghost_reference(row.episode_id, 7)


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("node"), reason="Node required")
@pytest.mark.parametrize("corruption", ["state", "fingerprint", "cadence"])
def test_cooking_cache_and_ghost_reject_corrupt_or_incompatible_recordings(
    session, tmp_path, corruption
):
    import json

    from harness_lab_app.gameplay.racing_service import CookingService

    service = CookingService(session, tmp_path)
    first = service.create_calibration()
    path = tmp_path / first.episode_id / "gameplay" / "replay.json"
    data = json.loads(path.read_text())
    if corruption == "state":
        data["frames"][10]["observation"]["state"]["actor"]["x"] += 1
    elif corruption == "fingerprint":
        data["environment_provenance"]["engine_sha256"] = "0" * 64
    else:
        data["config"]["ticks_per_action"] = 5
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        service.ghost_reference(first.episode_id, 7)
    current = service.create_calibration()
    assert current.state == "completed"
    assert current.episode_id != first.episode_id
    assert len(service.list_episodes()) == 2


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("node"), reason="Node required")
def test_fractional_model_station_is_recorded_as_invalid_action_without_advancing(
    session,
    tmp_path,
):
    from harness_lab_app.gameplay.contracts import PolicyReply
    from harness_lab_app.gameplay.cooking import CookingEnvironment
    from harness_lab_app.gameplay.racing_service import CookingService
    from harness_lab_app.gameplay.runner import run_episode
    from harness_lab_app.models import GameplayEpisode

    row = GameplayEpisode(
        episode_id="cook-" + "c" * 20,
        environment_id="harbor-kitchen",
        seed=7,
        controller="model",
    )
    service = CookingService(session, tmp_path)

    def invalid(observation, timeout):
        return PolicyReply(
            action_json=Action(
                observation_id=observation.observation_id,
                controls={"target": 1.5, "interact": 1.0},
            ).model_dump_json()
        )

    with CookingEnvironment() as env:
        replay = run_episode(env, invalid, service._config(row), tmp_path / "invalid")
    assert replay.result.status == "invalid_action"
    assert replay.result.simulation_ticks == 0
    assert (tmp_path / "invalid" / "replay.json").is_file()
