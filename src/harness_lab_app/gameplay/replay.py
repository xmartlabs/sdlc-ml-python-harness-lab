"""Durable episode artifacts and deterministic verification, not ghost rendering."""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from harness_lab_app.gameplay.contracts import EpisodeResult, Observation, Replay

if TYPE_CHECKING:
    from pydantic import BaseModel

    from harness_lab_app.gameplay.contracts import Environment, EpisodeConfig, Frame


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def observe(env: Environment, config: EpisodeConfig, tick: int) -> Observation:
    payload = Observation(
        episode_id=config.episode_id,
        observation_id="pending",
        tick=tick,
        ticks_to_advance=0
        if env.terminal()
        else min(config.ticks_per_action, config.max_ticks - tick),
        tick_rate=config.tick_rate,
        instructions=env.instructions,
        state=env.state(),
        controls=env.controls(),
        terminal=env.terminal(),
    )
    digest = hashlib.sha256(
        canonical(
            {
                "environment": [env.environment_id, env.environment_version],
                "config": config.model_dump(mode="json"),
                "observation": payload.model_dump(
                    mode="json", exclude={"observation_id"}
                ),
            }
        ).encode()
    ).hexdigest()
    return payload.model_copy(update={"observation_id": digest})


def summarize(
    config: EpisodeConfig, initial: Observation, frames: list[Frame], env: Environment
) -> EpisodeResult:
    last = frames[-1].observation if frames else initial
    failure = frames[-1].outcome if frames and frames[-1].outcome != "applied" else None
    latencies = [
        frame.latency_seconds
        for frame in frames
        if frame.provenance.get("model_call") is not False
    ]
    ordered = sorted(latencies)
    return EpisodeResult(
        status=failure or ("completed" if last.terminal else "budget_exhausted"),
        simulation_ticks=last.tick,
        simulation_seconds=last.tick / config.tick_rate,
        inference_seconds=sum(latencies),
        latency_p50_seconds=statistics.median(latencies) if latencies else 0.0,
        latency_p95_seconds=ordered[math.ceil(len(ordered) * 0.95) - 1]
        if ordered
        else 0.0,
        metrics={} if failure in ("provider_error", "timeout") else env.metrics(),
    )


class Recorder:
    """Exclusive directory; fsynced journal survives an interrupted episode."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory

    def append(self, kind: str, payload: BaseModel) -> None:
        with (self.directory / "journal.jsonl").open("a", encoding="utf-8") as file:
            file.write(
                canonical({"kind": kind, "payload": payload.model_dump(mode="json")})
                + "\n"
            )
            file.flush()
            os.fsync(file.fileno())

    def finish(self, replay: Replay) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary = Path(file.name)
                file.write(canonical(replay.model_dump(mode="json")) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.directory / "replay.json")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def verify_replay(path: Path, env: Environment) -> Replay:
    """Recompute every observation, event and numerical result from the actions.

    Detects inconsistent artifacts, not a malicious rewrite of an entire valid
    episode. Latency/provenance are recorded claims, not independently verified.
    """
    replay = Replay.model_validate_json(path.read_text())
    if (env.environment_id, env.environment_version) != (
        replay.environment_id,
        replay.environment_version,
    ):
        raise ValueError("Replay requires the original environment version")
    config = replay.config
    env.reset(config.seed)
    fingerprint = replay.environment_provenance.get("engine_sha256")
    if fingerprint and fingerprint != getattr(env, "provenance", {}).get(
        "engine_sha256"
    ):
        raise ValueError("Replay executable fingerprint mismatch")
    current = observe(env, config, 0)
    if current != replay.initial:
        raise ValueError("Initial state mismatch")
    failed = False
    for frame in replay.frames:
        if failed or current.terminal or current.tick >= config.max_ticks:
            raise ValueError("Frame after episode end")
        if frame.outcome == "applied":
            if frame.action is None or frame.ticks_advanced != current.ticks_to_advance:
                raise ValueError("Invalid applied action interval")
            if frame.latency_seconds > config.decision_timeout_seconds:
                raise ValueError("Late action was applied")
            frame.action.validate_for(current)
            events = env.step(frame.action.controls, frame.ticks_advanced)
            expected = observe(env, config, current.tick + frame.ticks_advanced)
        else:
            failed = True
            if frame.action is not None or frame.ticks_advanced != 0:
                raise ValueError("Failed decision changed state")
            events, expected = [], current
        if frame.events != events or frame.observation != expected:
            raise ValueError("Replay state or events mismatch")
        current = expected
    if not failed and not current.terminal and current.tick < config.max_ticks:
        raise ValueError("Incomplete episode")
    if summarize(config, replay.initial, replay.frames, env) != replay.result:
        raise ValueError("Result mismatch")
    return replay
