"""Frozen racing comparison and atomic file-backed evidence (no live SQLite access)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from harness_lab_app.config import get_settings
from harness_lab_app.gameplay.contracts import Replay
from harness_lab_app.gameplay.racing import RacingEnvironment

CONDITION: dict[str, Any] = {
    "version": "racing-data.v1",
    "environment_id": "tidal-ring-data-v1",
    "environment_version": "1",
    "track_id": "tidal-ring",
    "seed": 7,
    "tick_rate": 60,
    "ticks_per_action": 30,
    "max_ticks": 3780,
    "racing_horizon_seconds": 60,
    "decision_timeout_seconds": 60.0,
    "wall_budget_seconds": 1800,
    "observation_mode": "structured-text",
    "goal": "one valid lap through all twelve ordered checkpoints",
}


class BenchmarkEnvironment(RacingEnvironment):
    environment_id = "tidal-ring-data-v1"
    bridge_script = "scripts/racing_benchmark_bridge.mjs"
    engine_module = "frontend/src/gameplay/racingBenchmark.mjs"

    def __init__(self, root: Path | None = None, runtime_url: str | None = None):
        super().__init__(root, runtime_url)
        if not self.runtime_url:
            self.provenance["engine_sha256"] = hashlib.sha256(
                (self.root / self.engine_module).read_bytes()
                + (self.root / "frontend/src/gameplay/racing.mjs").read_bytes()
            ).hexdigest()


def condition_fingerprint(condition: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            condition, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def outcome_label(status: str, metrics: dict, reason: str = "") -> str:
    if reason == "cancelled" or status == "cancelled":
        return "cancelled"
    if reason == "wall_limit":
        return "inconclusive: wall limit"
    if status == "timeout":
        return "inconclusive: decision timeout"
    if status == "provider_error":
        return "inconclusive: provider error"
    if status == "invalid_action":
        return "invalid action"
    if metrics.get("finished") == 1:
        return "finished"
    return "DNF: simulation limit"


class ComparisonStore:
    """Only a trusted local driver writes; HTTP serves bounded finalized artifacts."""

    def __init__(self, root: Path | None = None):
        self.root = root or get_settings().repo_root / "data/racing-comparison"

    def current(self) -> dict:
        path = self.root / "current.json"
        if not path.exists():
            return {"condition": CONDITION, "competitors": [], "state": "not_started"}
        return json.loads(path.read_text())

    def write(self, value: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self.root, delete=False
            ) as f:
                temporary = Path(f.name)
                json.dump(value, f, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.root / "current.json")
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def path(self, episode_id: str) -> Path:
        if re.fullmatch(r"compare-[a-f0-9]{20}", episode_id):
            return self.root / "runs" / episode_id / "replay.json"
        if re.fullmatch(r"race-[a-f0-9]{20}", episode_id):
            return (
                get_settings().data_runs_dir / episode_id / "gameplay" / "replay.json"
            )
        raise LookupError("Comparison recording not found")

    def replay(self, episode_id: str) -> Replay:
        path = self.path(episode_id)
        if not path.is_file():
            raise LookupError("Comparison recording is not finalized")
        replay = Replay.model_validate_json(path.read_text())
        if replay.config.episode_id != episode_id:
            raise ValueError("Recording identity mismatch")
        return replay


comparison_router = APIRouter(prefix="/api/gameplay/racing/comparisons")


@comparison_router.get("/current")
def current_comparison():
    store = ComparisonStore()
    selected = store.root / "selection.json"
    return json.loads(selected.read_text()) if selected.is_file() else store.current()


@comparison_router.get("/episodes/{episode_id}/replay")
def comparison_replay(episode_id: str):
    try:
        return ComparisonStore().replay(episode_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, "Comparison recording is corrupt") from exc
