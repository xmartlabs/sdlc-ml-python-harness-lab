"""Bounded observe → decide → validate → step loop, with separate clocks."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic import ValidationError

from harness_lab_app.gameplay.contracts import Action, Frame, Replay, ReplayHeader
from harness_lab_app.gameplay.replay import Recorder, observe, summarize

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import JsonValue

    from harness_lab_app.gameplay.contracts import (
        DecisionOutcome,
        Environment,
        EpisodeConfig,
        Policy,
    )


def run_episode(
    env: Environment,
    policy: Policy,
    config: EpisodeConfig,
    directory: Path,
    *,
    clock: Callable[[], float] = time.monotonic,
    policy_provenance: dict[str, JsonValue] | None = None,
) -> Replay:
    """Abort invalid decisions; never silently repair, retry or advance time.

    Provider/timeouts are inconclusive (no game metrics). Environment/storage
    failures propagate, leaving the incremental journal for diagnosis.
    """
    recorder = Recorder(directory)
    env.reset(config.seed)
    initial = current = observe(env, config, 0)
    recorder.append(
        "header",
        ReplayHeader(
            environment_id=env.environment_id,
            environment_version=env.environment_version,
            environment_provenance=getattr(env, "provenance", {}),
            config=config,
            initial=initial,
            policy_provenance=policy_provenance or {},
        ),
    )
    frames: list[Frame] = []
    while not current.terminal and current.tick < config.max_ticks:
        started = clock()
        reply = None
        outcome: DecisionOutcome = "applied"
        try:
            reply = policy(
                current.model_copy(deep=True), config.decision_timeout_seconds
            )
        except TimeoutError:
            outcome = "timeout"
        except Exception:  # provider errors must not leak credentials into artifacts
            outcome = "provider_error"
        latency = max(0.0, clock() - started)
        if latency > config.decision_timeout_seconds:
            outcome = "timeout"
        action = None
        events: list[str] = []
        ticks = 0
        if outcome == "applied" and reply is not None:
            try:
                action = Action.model_validate_json(reply.action_json)
                action.validate_for(current)
            except (ValidationError, ValueError):
                action = None
                outcome = "invalid_action"
        if action is not None:
            ticks = current.ticks_to_advance
            events = env.step(action.controls, ticks)
            current = observe(env, config, current.tick + ticks)
        frame = Frame(
            action=action,
            observation=current,
            ticks_advanced=ticks,
            events=events,
            latency_seconds=latency,
            outcome=outcome,
            response_text=reply.action_json if reply else None,
            provenance=reply.provenance if reply else {},
        )
        frames.append(frame)
        recorder.append("frame", frame)
        if outcome != "applied":
            break
    replay = Replay(
        environment_id=env.environment_id,
        environment_version=env.environment_version,
        environment_provenance=getattr(env, "provenance", {}),
        config=config,
        initial=initial,
        policy_provenance=policy_provenance or {},
        frames=frames,
        result=summarize(config, initial, frames, env),
    )
    recorder.append("result", replay.result)
    recorder.finish(replay)
    return replay
