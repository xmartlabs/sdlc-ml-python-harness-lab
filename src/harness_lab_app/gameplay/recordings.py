"""Self-serve compatible recorded races; selections never overwrite frozen trials."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from harness_lab_app.config import get_settings
from harness_lab_app.gameplay.comparison import (
    CONDITION,
    BenchmarkEnvironment,
    ComparisonStore,
    outcome_label,
)
from harness_lab_app.gameplay.contracts import (
    Action,
    EpisodeCancelled,
    EpisodeConfig,
    PolicyReply,
    Replay,
)
from harness_lab_app.gameplay.racing import RacingEnvironment, baseline_policy
from harness_lab_app.gameplay.replay import verify_replay
from harness_lab_app.gameplay.runner import run_episode

RULE_KEYS = (
    "environment_id",
    "environment_version",
    "seed",
    "tick_rate",
    "ticks_per_action",
    "max_ticks",
    "engine_sha256",
)


def compatible_condition(descriptors: list[dict]) -> dict:
    if not 1 <= len(descriptors) <= 8:
        raise ValueError("Choose one to eight recordings")
    condition = {key: descriptors[0].get(key) for key in RULE_KEYS}
    if not condition["engine_sha256"] or any(
        {key: d.get(key) for key in RULE_KEYS} != condition for d in descriptors
    ):
        raise ValueError(
            "Recordings require the same engine, track, seed, cadence and horizon"
        )
    return condition


def describe(replay: Replay) -> dict[str, Any]:
    policy = replay.policy_provenance
    controller = str(policy.get("controller", "model"))
    controller = (
        "human"
        if controller.startswith("human")
        else "calibration"
        if ("calibration" in controller or "scripted" in controller.lower())
        else "model"
    )
    model = str(policy.get("requested_model") or "")
    label = (
        ("Human " + replay.config.episode_id[-5:])
        if controller == "human"
        else str(policy.get("label") or "SCRIPTED calibration")
        if controller == "calibration"
        else model or "AI recording (model unreported)"
    )
    last = replay.frames[-1].observation if replay.frames else replay.initial
    return {
        "episode_id": replay.config.episode_id,
        "label": label,
        "controller": controller,
        "model": model,
        "environment_id": replay.environment_id,
        "environment_version": replay.environment_version,
        "seed": replay.config.seed,
        "tick_rate": replay.config.tick_rate,
        "ticks_per_action": replay.config.ticks_per_action,
        "max_ticks": replay.config.max_ticks,
        "engine_sha256": replay.environment_provenance.get("engine_sha256"),
        "state": "finalized",
        "status": outcome_label(replay.result.status, replay.result.metrics),
        "metrics": last.state.get("metrics", replay.result.metrics),
        "result": replay.result.model_dump(mode="json"),
        "live_state": last.state,
        "policy": policy,
    }


def atomic_selection(store: ComparisonStore, value: dict) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=store.root, delete=False) as f:
            temporary = Path(f.name)
            json.dump(value, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, store.root / "selection.json")
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def make_selection(store: ComparisonStore, ids: list[str]) -> dict:
    if len(ids) != len(set(ids)):
        raise ValueError("Choose distinct recordings")
    descriptors = []
    for ident in ids:
        replay = store.replay(ident)
        env_type = (
            BenchmarkEnvironment
            if replay.environment_id == "tidal-ring-data-v1"
            else RacingEnvironment
        )
        with env_type() as env:
            verify_replay(store.path(ident), env)
        descriptors.append(describe(replay))
    condition = compatible_condition(descriptors)
    selection = {
        "condition": condition,
        "competitors": descriptors,
        "state": "completed",
        "session_id": uuid.uuid4().hex,
    }
    atomic_selection(store, selection)
    return selection


def catalog(store: ComparisonStore) -> list[dict]:
    candidates = list((store.root / "runs").glob("compare-*/replay.json")) + list(
        get_settings().data_runs_dir.glob("race-*/gameplay/replay.json")
    )
    items = []
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
        try:
            replay = store.replay(
                path.parent.name
                if path.parent.name.startswith("compare-")
                else path.parent.parent.name
            )
            items.append(describe(replay))
        except (ValueError, LookupError, OSError):
            continue
    return items


router = APIRouter(prefix="/api/gameplay/racing/comparisons")


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_ids: list[str] = Field(min_length=1, max_length=8)


@router.get("/recordings")
def recordings():
    return catalog(ComparisonStore())


@router.post("/sessions", status_code=201)
def select_recordings(data: Selection):
    try:
        return make_selection(ComparisonStore(), data.episode_ids)
    except (ValueError, LookupError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            503, "Cannot verify recording; check the game runtime"
        ) from exc


@router.post("/examples", status_code=201)
def examples():
    store = ComparisonStore()
    ids = []
    try:
        for label in ["A", "B"]:
            ident = "compare-" + uuid.uuid4().hex[:20]
            with RacingEnvironment() as env:
                run_episode(
                    env,
                    baseline_policy(env),
                    EpisodeConfig(
                        episode_id=ident,
                        seed=7,
                        tick_rate=60,
                        ticks_per_action=12,
                        max_ticks=7200,
                    ),
                    store.root / "runs" / ident,
                    clock=lambda: 0.0,
                    policy_provenance={
                        "controller": "scripted-calibration",
                        "model_call": False,
                        "label": "SCRIPTED reference " + label,
                    },
                )
            ids.append(ident)
        return make_selection(store, ids)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(
            503, "Cannot create references; check the game runtime"
        ) from exc


class HumanRecording(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    seed: int = Field(default=7, ge=0, le=2**32 - 1)
    actions: list[dict[str, float]] = Field(min_length=1, max_length=126)


@router.post("/human", status_code=201)
def save_benchmark_human(data: HumanRecording):
    store = ComparisonStore()
    ident = "compare-" + uuid.uuid4().hex[:20]
    index = 0

    def policy(observation, timeout):  # noqa: ARG001 - shared policy port
        nonlocal index
        if index >= len(data.actions):
            raise EpisodeCancelled()
        controls = data.actions[index]
        index += 1
        return PolicyReply(
            action_json=Action(
                observation_id=observation.observation_id, controls=controls
            ).model_dump_json(),
            provenance={"model_call": False},
        )

    try:
        with BenchmarkEnvironment() as env:
            replay = run_episode(
                env,
                policy,
                EpisodeConfig(
                    episode_id=ident,
                    seed=data.seed,
                    tick_rate=60,
                    ticks_per_action=30,
                    max_ticks=CONDITION["max_ticks"],
                ),
                store.root / "runs" / ident,
                clock=lambda: 0.0,
                policy_provenance={
                    "controller": "human-recorded-controls",
                    "model_call": False,
                },
            )
        if replay.result.status == "invalid_action":
            raise ValueError("Human recording contains invalid controls")
        return describe(replay)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            503, "Cannot verify human recording; check the game runtime"
        ) from exc


@router.delete("/sessions/current")
def clear_selection():
    """Leave official evidence intact when returning from a saved selection."""
    store = ComparisonStore()
    (store.root / "selection.json").unlink(missing_ok=True)
    return store.current()
