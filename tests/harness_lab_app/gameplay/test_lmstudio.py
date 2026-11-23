"""The real HTTP boundary is exercised against a local fake server, never a model."""

import json
import subprocess
import sys
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from harness_lab_app.gameplay.contracts import EpisodeConfig
from harness_lab_app.gameplay.fixture import LineWorld
from harness_lab_app.gameplay.lmstudio import LMStudioPolicy
from harness_lab_app.gameplay.runner import run_episode


@pytest.fixture
def server():
    requests = []
    loaded = [True]
    delay = [0.0]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.path == "/api/v1/models"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "models": [
                            {
                                "key": "test/model",
                                "type": "llm",
                                "loaded_instances": [
                                    {
                                        "id": "instance-1",
                                        "config": {"context_length": 4096},
                                    }
                                ]
                                if loaded[0]
                                else [],
                                "quantization": {"name": "Q4"},
                            }
                        ]
                    }
                ).encode()
            )

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, payload))
            time.sleep(delay[0])
            observation = json.loads(
                payload["messages"][0]["content"].split("OBSERVATION\n")[1]
            )
            self.send_response(200)
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(
                    json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": json.dumps(
                                            {
                                                "version": "gameplay.v1",
                                                "observation_id": observation[
                                                    "observation_id"
                                                ],
                                                "controls": {"move": 1.0},
                                            }
                                        )
                                    }
                                }
                            ],
                            "usage": {"prompt_tokens": 70, "completion_tokens": 30},
                        }
                    ).encode()
                )

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/v1", requests, loaded, delay
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


@pytest.mark.integration
def test_direct_policy_uses_loaded_instance_and_records_usage(tmp_path, server):
    url, requests, _, _ = server
    policy = LMStudioPolicy.connect(url, "lmstudio/test/model")
    replay = run_episode(
        LineWorld(),
        policy,
        EpisodeConfig(episode_id="http-test", seed=7),
        tmp_path / "episode",
    )
    assert replay.result.status == "completed"
    assert len(requests) == 2
    assert requests[0][0] == "/api/v0/chat/completions"
    assert requests[0][1]["model"] == "instance-1"
    assert requests[0][1]["max_tokens"] == 512
    provenance = replay.frames[0].provenance
    assert provenance["requested_model"] == "test/model"
    assert provenance["instance_id"] == "instance-1"
    assert provenance["prompt_tokens"] == 70
    assert provenance["completion_tokens"] == 30
    assert provenance["effective_temperature"] is None


@pytest.mark.integration
def test_connect_refuses_unloaded_or_unknown_models_without_completion(server):
    url, requests, loaded, _ = server
    loaded[0] = False
    with pytest.raises(ValueError, match="loaded"):
        LMStudioPolicy.connect(url, "test/model")
    with pytest.raises(ValueError, match="Unknown"):
        LMStudioPolicy.connect(url, "not-present")
    assert requests == []


@pytest.mark.integration
def test_cli_runs_and_verifies_a_persisted_fixture(tmp_path):
    root = Path(__file__).resolve().parents[3]
    command = [sys.executable, str(root / "scripts/gameplay_episode.py")]
    destination = tmp_path / "episode"
    run = subprocess.run(
        [*command, "--output", str(destination), "--episode-id", "cli-test"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)["status"] == "completed"
    verified = subprocess.run(
        [*command, "--verify", str(destination / "replay.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr


@pytest.mark.integration
def test_http_timeout_is_persisted_without_advancing_game_time(tmp_path, server):
    url, _, _, delay = server
    delay[0] = 0.1
    policy = LMStudioPolicy.connect(url, "test/model")
    replay = run_episode(
        LineWorld(),
        policy,
        EpisodeConfig(episode_id="slow-http", seed=7, decision_timeout_seconds=0.02),
        tmp_path / "episode",
        policy_provenance=policy.provenance,
    )
    assert replay.result.status == "timeout"
    assert replay.result.simulation_ticks == 0
    assert replay.result.metrics == {}
    assert (tmp_path / "episode" / "replay.json").exists()
