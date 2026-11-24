"""The Python recorder and browser use one simulation, across the Node boundary."""

import shutil

import pytest

from harness_lab_app.gameplay.contracts import EpisodeConfig
from harness_lab_app.gameplay.racing import RacingEnvironment, baseline_policy
from harness_lab_app.gameplay.replay import verify_replay
from harness_lab_app.gameplay.runner import run_episode

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not shutil.which("node"), reason="Node is required for shared racing simulation"
    ),
]


def test_baseline_completes_real_course_and_verifies_recording(tmp_path):
    with RacingEnvironment() as env:
        replay = run_episode(
            env,
            baseline_policy(env),
            EpisodeConfig(
                episode_id="race-reference",
                seed=7,
                tick_rate=60,
                ticks_per_action=12,
                max_ticks=7200,
            ),
            tmp_path / "episode",
        )
    assert replay.result.metrics["finished"] == 1
    assert replay.result.metrics["progress"] == 1
    assert replay.result.metrics["finish_time_seconds"] > 10
    with RacingEnvironment() as env:
        assert verify_replay(tmp_path / "episode" / "replay.json", env) == replay


def test_batched_node_simulation_has_identical_state():
    with RacingEnvironment() as a, RacingEnvironment() as b:
        a.reset(2)
        b.reset(2)
        controls = {"throttle": 1.0, "brake": 0.0, "steer": 0.2, "reset": 0.0}
        a.step(controls, 240)
        for _ in range(20):
            b.step(controls, 12)
        assert a.state() == b.state()


def test_finish_preserves_exact_game_time_and_consumes_the_action_interval():
    with RacingEnvironment() as env:
        env.reset(7)
        ticks = 0
        while not env.terminal():
            controls = env._request({"op": "baseline"})["baseline"]
            env.step(controls, 12)
            ticks += 12
        assert env.state()["tick"] == ticks
        assert env.metrics()["finish_time_seconds"] < ticks / 60 - 3


def test_remote_runtime_matches_local_and_closes_session(tmp_path):
    import json
    import os
    import socket
    import subprocess
    import time
    import urllib.request
    from pathlib import Path

    # Test-owned service and port, never the operator's Compose deployment.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    root = Path(__file__).resolve().parents[3]
    process = subprocess.Popen(
        ["node", str(root / "game-runtime/server.mjs")],
        env={**os.environ, "PORT": str(port)},
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(base + "/health", timeout=0.1):  # nosec B310 - test-owned loopback HTTP
                    break
            except OSError:
                time.sleep(0.02)
        with (
            RacingEnvironment(runtime_url=base) as remote,
            RacingEnvironment(runtime_url="") as local,
        ):
            remote.reset(7)
            local.reset(7)
            for _ in range(30):
                controls = local._request({"op": "baseline"})["baseline"]
                assert remote.step(controls, 12) == local.step(controls, 12)
                assert remote.state() == local.state()
                assert remote.metrics() == local.metrics()
            assert remote.provenance["engine_sha256"]
        with urllib.request.urlopen(base + "/health") as response:  # nosec B310 - test-owned loopback HTTP
            assert json.load(response)["sessions"] == 0
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_runtime_rejects_non_http_schemes():
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        RacingEnvironment(runtime_url="file:///tmp/not-a-runtime")
