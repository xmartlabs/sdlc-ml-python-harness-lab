"""PR1 Environment port backed by the same ESM simulation the browser renders."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import selectors
import shutil
import subprocess  # nosec B404 - fixed local Node program, never model-supplied code
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_lab_app.gameplay.contracts import Action, ControlBounds, PolicyReply

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

    from harness_lab_app.gameplay.contracts import Observation, Policy


class RacingEnvironment:
    environment_id = "tidal-ring"
    environment_version = "1"
    bridge_script = "scripts/racing_bridge.mjs"
    engine_module = "frontend/src/gameplay/racing.mjs"
    instructions = (
        "Finish one valid lap through the ordered checkpoints. You control a car on "
        "a fixed circuit. throttle and brake are 0..1; steer is -1..1 with the same "
        "sign as heading_error (radians toward the lookahead point). Keep near the "
        "road center, slow for upcoming_turn, and avoid barriers. reset=1 returns "
        "to the last checkpoint without gaining progress and incurs a time penalty. "
        "Return every control. Countdown is non-interactive."
    )

    def __init__(self, root: Path | None = None, runtime_url: str | None = None):
        self.runtime_url = (
            runtime_url
            if runtime_url is not None
            else os.environ.get("HARNESS_LAB_GAMEPLAY_RUNTIME_URL", "")
        ).rstrip("/")
        if self.runtime_url and urllib.parse.urlsplit(self.runtime_url).scheme not in {
            "http",
            "https",
        }:
            raise ValueError("Game runtime URL must use HTTP or HTTPS")
        self.session_id = ""
        self.current: dict[str, Any] = {}
        self.provenance: dict[str, Any] = {
            "transport": "http-runtime" if self.runtime_url else "node-subprocess"
        }
        self.process = None
        if self.runtime_url:
            return
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("Racing requires Node.js 20 or newer")
        self.root = root or Path(__file__).resolve().parents[3]
        self.provenance["engine_sha256"] = hashlib.sha256(
            (self.root / self.engine_module).read_bytes()
        ).hexdigest()
        self.process = subprocess.Popen(  # nosec B603 - fixed trusted local bridge
            [node, str(self.root / self.bridge_script)],
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )

    def _request(self, payload: dict) -> dict:
        if self.runtime_url:
            operation = payload["op"]
            if operation == "reset":
                self._close_remote()
                path, body = (
                    "/v1/sessions",
                    {"seed": payload["seed"], "environment": self.environment_id},
                )
            else:
                if not self.session_id:
                    raise RuntimeError("Reset required before runtime operation")
                path = f"/v1/sessions/{self.session_id}/{operation}"
                body = {key: value for key, value in payload.items() if key != "op"}
            reply = self._http(path, body)
            if operation == "reset":
                self.session_id = reply["session_id"]
                self.provenance.update(
                    {key: reply[key] for key in ("engine_sha256", "node_version")}
                )
            self.current = reply
            return reply
        assert self.process is not None
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("Racing bridge is closed")
        self.process.stdin.write(json.dumps(payload, allow_nan=False) + "\n")
        self.process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=10):
                raise RuntimeError("Racing simulation did not respond")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("Racing simulation exited")
        reply = json.loads(line)
        if "error" in reply:
            raise RuntimeError(reply["error"])
        self.current = reply
        return reply

    def reset(self, seed: int) -> None:
        self._request({"op": "reset", "seed": seed})

    def state(self) -> dict[str, JsonValue]:
        return self.current["state"]

    @staticmethod
    def controls() -> dict[str, ControlBounds]:
        return {
            name: ControlBounds(minimum=-1.0 if name == "steer" else 0.0, maximum=1.0)
            for name in ("throttle", "brake", "steer", "reset")
        }

    def step(self, controls: dict[str, float], ticks: int) -> list[str]:
        return self._request({"op": "step", "controls": controls, "ticks": ticks})[
            "events"
        ]

    def terminal(self) -> bool:
        return self.current["terminal"]

    def metrics(self) -> dict[str, float]:
        return self.current["metrics"]

    def _http(self, path: str, body: dict | None = None, method: str = "POST") -> dict:
        request = urllib.request.Request(
            self.runtime_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310 - configured HTTP(S) runtime, scheme validated above
            return json.load(response)

    def _close_remote(self) -> None:
        if self.session_id:
            with contextlib.suppress(OSError):
                self._http(f"/v1/sessions/{self.session_id}", method="DELETE")
            self.session_id = ""

    def close(self) -> None:
        if self.runtime_url:
            self._close_remote()
            return
        assert self.process is not None
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()

    def __enter__(self) -> RacingEnvironment:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def baseline_policy(env: RacingEnvironment) -> Callable:
    """Calibration driver: not a model, never included in model score aggregates."""

    def decide(observation: Observation, timeout: float) -> PolicyReply:  # noqa: ARG001
        controls = env._request({"op": "baseline"})["baseline"]
        return PolicyReply(
            action_json=Action(
                observation_id=observation.observation_id, controls=controls
            ).model_dump_json(),
            provenance={"controller": "calibration"},
        )

    return decide


def after_countdown(policy: Policy) -> Callable:
    """The fixed start sequence cannot be sped up or influenced by a model."""

    def decide(observation: Observation, timeout: float) -> PolicyReply:
        if observation.state.get("phase") == "countdown":
            return PolicyReply(
                action_json=Action(
                    observation_id=observation.observation_id,
                    controls={
                        "throttle": 0.0,
                        "brake": 0.0,
                        "steer": 0.0,
                        "reset": 0.0,
                    },
                ).model_dump_json(),
                provenance={"controller": "fixed-countdown", "model_call": False},
            )
        return policy(observation, timeout)

    return decide
