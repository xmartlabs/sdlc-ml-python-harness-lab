"""Persistent fixed-game episodes: DB identity/results plus PR1 replay artifacts."""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sqlmodel import select

from harness_lab_app.config import get_settings
from harness_lab_app.gameplay.contracts import (
    Action,
    EpisodeConfig,
    PolicyReply,
    Replay,
)
from harness_lab_app.gameplay.lmstudio import LMStudioPolicy
from harness_lab_app.gameplay.racing import (
    RacingEnvironment,
    after_countdown,
    baseline_policy,
)
from harness_lab_app.gameplay.replay import verify_replay
from harness_lab_app.gameplay.runner import run_episode
from harness_lab_app.llm_transport import setting
from harness_lab_app.models import GameplayEpisode, utc_now

if TYPE_CHECKING:
    from sqlmodel import Session

    from harness_lab_app.gameplay.contracts import Observation


ZERO_CONTROLS = {"throttle": 0.0, "brake": 0.0, "steer": 0.0, "reset": 0.0}


def model_url() -> str:
    base = setting("LMSTUDIO_BASE_URL") or "http://127.0.0.1:1234/v1"
    if Path("/.dockerenv").exists():
        from harness_lab_app.executors import rewrite_localhost

        base = rewrite_localhost(base, get_settings().docker_host_alias)
    return base


def enqueue_race(episode_id: str) -> None:
    from harness_lab_app.celery_app import celery_app

    celery_app.send_task(
        "harness_lab_app.gameplay.tasks.execute_race",
        args=[episode_id],
        queue="runs",
        retry=False,
    )


class RacingService:
    def __init__(self, session: Session, runs_dir: Path | None = None):
        self.session = session
        self.runs_dir = runs_dir or get_settings().data_runs_dir

    def list_episodes(self) -> list[GameplayEpisode]:
        rows = list(
            self.session.exec(
                select(GameplayEpisode)
                .order_by(cast("Any", GameplayEpisode.created_at).desc())
                .limit(50)
            )
        )
        for row in rows:
            age = (
                utc_now() - row.updated_at.replace(tzinfo=utc_now().tzinfo)
            ).total_seconds()
            if row.state == "running" and age > 660:
                row.cancel_requested = True
                row.state = "failed"
                row.failure_reason = "Worker stopped responding or exceeded the wall budget; start a new attempt."
                self._save(row)
        return rows

    def get(self, episode_id: str) -> GameplayEpisode:
        if not re.fullmatch(r"race-[0-9a-f]{20}", episode_id):
            raise LookupError("Race episode not found")
        row = self.session.get(GameplayEpisode, episode_id)
        if row is None:
            raise LookupError("Race episode not found")
        return row

    def replay(self, episode_id: str) -> Replay:
        row = self.get(episode_id)
        path = self.runs_dir / row.episode_id / "gameplay" / "replay.json"
        if not path.is_file():
            raise LookupError("Recording is not finalized")
        return Replay.model_validate_json(path.read_text())

    def ghost_reference(self, episode_id: str, seed: int) -> dict[str, Any]:
        row = self.get(episode_id)
        if row.controller not in ("model", "calibration"):
            raise ValueError("Choose a recorded model or scripted calibration run")
        replay = self.replay(episode_id)
        config = replay.config
        if config.seed != seed or (
            config.tick_rate,
            config.ticks_per_action,
            config.max_ticks,
        ) != (60, 12, 7200):
            raise ValueError("Ghost requires the same seed and control cadence")
        if not replay.environment_provenance.get("engine_sha256"):
            raise ValueError("Ghost recording has no engine fingerprint")
        with RacingEnvironment() as env:
            verify_replay(self.runs_dir / episode_id / "gameplay" / "replay.json", env)
        last = replay.frames[-1].observation if replay.frames else replay.initial
        progress = last.state.get("progress", 0)
        if last.tick <= 180 or not isinstance(progress, (int, float)) or progress <= 0:
            raise ValueError(
                "Recording has no meaningful racing movement after countdown"
            )
        return {
            "episode_id": episode_id,
            "label": "SCRIPTED calibration"
            if row.controller == "calibration"
            else row.model,
            "partial": last.state.get("phase") != "finished",
            "status": replay.result.status,
            "finish_time_seconds": replay.result.metrics.get("finish_time_seconds"),
            "recording": replay.model_dump(mode="json"),
        }

    def create_calibration(self) -> GameplayEpisode:
        """Explicit reference instrument, never counted as model performance."""
        row = self._new(7, "calibration", "SCRIPTED calibration")
        self._save(row)
        try:
            with RacingEnvironment() as env:
                replay = run_episode(
                    env,
                    baseline_policy(env),
                    self._config(row),
                    self.runs_dir / row.episode_id / "gameplay",
                    clock=lambda: 0.0,
                    policy_provenance={
                        "controller": "scripted-calibration",
                        "model_call": False,
                    },
                )
            row.result_json = replay.result.model_dump(mode="json")
            row.state = "completed"
        except Exception:
            row.state = "failed"
            row.failure_reason = "Calibration could not run; check the game runtime."
        return self._save(row)

    def _save(self, row: GameplayEpisode) -> GameplayEpisode:
        row.updated_at = utc_now()
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def _new(self, seed: int, controller: str, model: str = "") -> GameplayEpisode:
        return GameplayEpisode(
            episode_id="race-" + uuid.uuid4().hex[:20],
            seed=seed,
            controller=controller,
            model=model,
        )

    def launch_model(self, seed: int, model: str) -> GameplayEpisode:
        policy = LMStudioPolicy.connect(model_url(), model)
        row = self._new(seed, "model", model)
        row.policy_json = policy.provenance
        self._save(row)
        try:
            enqueue_race(row.episode_id)
        except Exception:
            row.state = "failed"
            row.failure_reason = (
                "Could not queue model episode; check the worker and broker."
            )
            self._save(row)
        return row

    def cancel(self, episode_id: str) -> GameplayEpisode:
        row = self.get(episode_id)
        if row.state in ("pending", "running"):
            row.cancel_requested = True
            row.state = "cancelled"
            self._save(row)
        return row

    def save_human(
        self,
        seed: int,
        controls: list[dict[str, float]],
        ghost_episode_id: str | None = None,
    ) -> GameplayEpisode:
        if not 1 <= len(controls) <= 600:
            raise ValueError("Supply 1 to 600 action intervals")
        # Validate all input before creating an episode or stepping the simulation.
        for item in controls:
            if set(item) != set(ZERO_CONTROLS) or any(
                not isinstance(v, (int, float))
                or isinstance(v, bool)
                or not -1 <= v <= 1
                for v in item.values()
            ):
                raise ValueError("Invalid racing controls")
            if any(item[k] < 0 for k in ("throttle", "brake", "reset")):
                raise ValueError("Invalid racing controls")
        ghost = (
            self.ghost_reference(ghost_episode_id, seed) if ghost_episode_id else None
        )
        row = self._new(seed, "human")
        if ghost is not None:
            row.policy_json = {
                "ghost_episode_id": ghost_episode_id,
                "ghost_engine_sha256": ghost["recording"]["environment_provenance"][
                    "engine_sha256"
                ],
            }
        self._save(row)
        index = 0

        def policy(observation: Observation, timeout: float) -> PolicyReply:  # noqa: ARG001
            nonlocal index
            value = controls[index] if index < len(controls) else ZERO_CONTROLS
            index += 1
            return PolicyReply(
                action_json=Action(
                    observation_id=observation.observation_id, controls=value
                ).model_dump_json()
            )

        try:
            with RacingEnvironment() as env:
                replay = run_episode(
                    env,
                    policy,
                    self._config(row),
                    self.runs_dir / row.episode_id / "gameplay",
                    clock=lambda: 0.0,
                    policy_provenance={
                        **row.policy_json,
                        "controller": "human-recorded-controls",
                        "input_cadence_hz": 5,
                    },
                )
            row.result_json = replay.result.model_dump(mode="json")
            row.state = "completed"
        except Exception:
            row.state = "failed"
            row.failure_reason = (
                "Human recording could not be simulated; check the Node runtime."
            )
        return self._save(row)

    @staticmethod
    def _config(row: GameplayEpisode) -> EpisodeConfig:
        return EpisodeConfig(
            episode_id=row.episode_id,
            seed=row.seed,
            tick_rate=60,
            ticks_per_action=12,
            max_ticks=7200,
            decision_timeout_seconds=30.0,
        )

    def execute(self, episode_id: str) -> None:
        row = self.get(episode_id)
        if row.state != "pending":
            return
        row.state = "running"
        self._save(row)
        started = time.monotonic()
        try:
            policy = LMStudioPolicy.connect(model_url(), row.model)
            if policy.instance_id != row.policy_json.get("instance_id"):
                raise RuntimeError("Model instance changed")
            model_decide = after_countdown(policy)

            def decide(observation: Observation, timeout: float) -> PolicyReply:
                self.session.refresh(row)
                if row.cancel_requested or time.monotonic() - started >= 600:
                    raise RuntimeError("Episode cancelled or wall budget exhausted")
                return model_decide(observation, timeout)

            with RacingEnvironment() as env:
                replay = run_episode(
                    env,
                    decide,
                    self._config(row),
                    self.runs_dir / row.episode_id / "gameplay",
                    policy_provenance={
                        **policy.provenance,
                        "countdown_controller": "fixed-noop",
                        "wall_budget_seconds": 600,
                    },
                )
            self.session.refresh(row)
            row.result_json = replay.result.model_dump(mode="json")
            row.state = (
                "cancelled"
                if row.cancel_requested
                else (
                    "completed"
                    if replay.result.status in ("completed", "budget_exhausted")
                    else "failed"
                )
            )
            row.failure_reason = (
                ""
                if row.state == "completed"
                else "Episode stopped: " + replay.result.status
            )
        except Exception:
            self.session.refresh(row)
            row.state = "cancelled" if row.cancel_requested else "failed"
            row.failure_reason = "Model episode failed; check its loaded instance, server and Node runtime."
        self._save(row)
