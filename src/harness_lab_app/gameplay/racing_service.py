"""Persistent fixed-game episodes: DB identity/results plus PR1 replay artifacts."""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from sqlmodel import select

from harness_lab_app.config import get_settings
from harness_lab_app.gameplay.contracts import (
    Action,
    EpisodeCancelled,
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
    environment_type = RacingEnvironment
    episode_prefix = "race-"
    tick_rate = 60
    action_ticks = 12
    max_ticks = 7200
    zero_controls: ClassVar[dict[str, float]] = ZERO_CONTROLS
    countdown_policy = staticmethod(after_countdown)

    @property
    def max_actions(self) -> int:
        return (self.max_ticks + self.action_ticks - 1) // self.action_ticks

    def _cadence(self) -> tuple[int, int, int]:
        return self.tick_rate, self.action_ticks, self.max_ticks

    def _calibration_succeeded(self, replay: Replay) -> bool:
        return replay.result.metrics.get("finished") == 1

    def _meaningful_ghost(self, replay: Replay) -> bool:
        last = replay.frames[-1].observation if replay.frames else replay.initial
        progress = last.state.get("progress", 0)
        return last.tick > 180 and isinstance(progress, (int, float)) and progress > 0

    def _ghost_partial(self, replay: Replay) -> bool:
        last = replay.frames[-1].observation if replay.frames else replay.initial
        return last.state.get("phase") != "finished"

    def _validate_human_controls(self, controls: list[dict[str, float]]) -> None:
        if not 1 <= len(controls) <= self.max_actions:
            raise ValueError(f"Supply 1 to {self.max_actions} action intervals")
        bounds = self.environment_type.controls()
        for item in controls:
            if set(item) != set(bounds):
                raise ValueError("Supply exactly the advertised controls")
            for name, value in item.items():
                bound = bounds[name]
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not bound.minimum <= value <= bound.maximum
                    or (bound.integer and not float(value).is_integer())
                ):
                    raise ValueError("Invalid gameplay controls")

    def __init__(self, session: Session, runs_dir: Path | None = None):
        self.session = session
        self.runs_dir = runs_dir or get_settings().data_runs_dir

    def list_episodes(self) -> list[GameplayEpisode]:
        rows = list(
            self.session.exec(
                select(GameplayEpisode)
                .where(
                    GameplayEpisode.environment_id
                    == self.environment_type.environment_id
                )
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
        if not re.fullmatch(
            re.escape(self.episode_prefix) + r"[0-9a-f]{20}", episode_id
        ):
            raise LookupError("Race episode not found")
        row = self.session.get(GameplayEpisode, episode_id)
        if row is None or row.environment_id != self.environment_type.environment_id:
            raise LookupError("Game episode not found")
        return row

    def replay(self, episode_id: str) -> Replay:
        row = self.get(episode_id)
        path = self.runs_dir / row.episode_id / "gameplay" / "replay.json"
        if not path.is_file():
            raise LookupError("Recording is not finalized")
        return Replay.model_validate_json(path.read_text())

    def ghost_reference(self, episode_id: str, seed: int) -> dict[str, Any]:
        row = self.get(episode_id)
        if row.controller not in ("human", "model", "calibration"):
            raise ValueError("Choose a recorded human, AI or scripted calibration run")
        replay = self.replay(episode_id)
        config = replay.config
        if (
            config.seed != seed
            or (
                config.tick_rate,
                config.ticks_per_action,
                config.max_ticks,
            )
            != self._cadence()
        ):
            raise ValueError("Ghost requires the same seed and control cadence")
        if not replay.environment_provenance.get("engine_sha256"):
            raise ValueError("Ghost recording has no engine fingerprint")
        with self.environment_type() as env:
            verify_replay(self.runs_dir / episode_id / "gameplay" / "replay.json", env)
        if not self._meaningful_ghost(replay):
            raise ValueError("Recording has no meaningful movement after countdown")
        return {
            "episode_id": episode_id,
            "label": "SCRIPTED calibration"
            if row.controller == "calibration"
            else "Human " + row.episode_id[-5:]
            if row.controller == "human"
            else row.model,
            "partial": self._ghost_partial(replay),
            "status": replay.result.status,
            "finish_time_seconds": replay.result.metrics.get("finish_time_seconds"),
            "recording": replay.model_dump(mode="json"),
        }

    def _cached_calibration(self, env: RacingEnvironment) -> GameplayEpisode | None:
        fingerprint = env.provenance.get("engine_sha256")
        if not fingerprint:
            return None
        candidates = self.session.exec(
            select(GameplayEpisode)
            .where(
                GameplayEpisode.environment_id == self.environment_type.environment_id,
                GameplayEpisode.controller == "calibration",
                GameplayEpisode.state == "completed",
                GameplayEpisode.seed == 7,
            )
            .order_by(cast("Any", GameplayEpisode.created_at).desc())
        )
        for row in candidates:
            try:
                replay = self.replay(row.episode_id)
            except (ValueError, LookupError, OSError):
                continue  # Missing or malformed stored artifact, not runtime I/O.
            if (
                replay.environment_provenance.get("engine_sha256") != fingerprint
                or replay.config.seed != 7
                or (
                    replay.config.tick_rate,
                    replay.config.ticks_per_action,
                    replay.config.max_ticks,
                )
                != self._cadence()
                or not self._calibration_succeeded(replay)
            ):
                continue
            try:
                verify_replay(
                    self.runs_dir / row.episode_id / "gameplay" / "replay.json", env
                )
            except (ValueError, FileNotFoundError):
                continue  # Corrupt/disappearing artifact; transport errors propagate.
            return row
        return None

    def create_calibration(self) -> GameplayEpisode:
        """Reuse a verified reference for this build; never a model performance."""
        with self.environment_type() as env:
            env.reset(7)
            cached = self._cached_calibration(env)
            if cached is not None:
                return cached
            row = self._new(7, "calibration", "SCRIPTED calibration")
            self._save(row)
            try:
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
                if not self._calibration_succeeded(replay):
                    raise ValueError(
                        "Scripted calibration did not complete the objective"
                    )
                row.result_json = replay.result.model_dump(mode="json")
                row.state = "completed"
            except Exception:
                row.state = "failed"
                row.failure_reason = (
                    "Calibration could not run; check the game runtime."
                )
            return self._save(row)

    def _save(self, row: GameplayEpisode) -> GameplayEpisode:
        row.updated_at = utc_now()
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def _new(self, seed: int, controller: str, model: str = "") -> GameplayEpisode:
        return GameplayEpisode(
            episode_id=self.episode_prefix + uuid.uuid4().hex[:20],
            environment_id=self.environment_type.environment_id,
            environment_version=self.environment_type.environment_version,
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
        self._validate_human_controls(controls)
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
            if index >= len(controls):
                raise EpisodeCancelled()
            value = controls[index]
            index += 1
            return PolicyReply(
                action_json=Action(
                    observation_id=observation.observation_id, controls=value
                ).model_dump_json()
            )

        try:
            with self.environment_type() as env:
                replay = run_episode(
                    env,
                    policy,
                    self._config(row),
                    self.runs_dir / row.episode_id / "gameplay",
                    clock=lambda: 0.0,
                    policy_provenance={
                        **row.policy_json,
                        "controller": "human-recorded-controls",
                        "input_cadence_hz": self.tick_rate / self.action_ticks,
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

    def _config(self, row: GameplayEpisode) -> EpisodeConfig:
        return EpisodeConfig(
            episode_id=row.episode_id,
            seed=row.seed,
            tick_rate=self.tick_rate,
            ticks_per_action=self.action_ticks,
            max_ticks=self.max_ticks,
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
            model_decide = self.countdown_policy(policy)

            def decide(observation: Observation, timeout: float) -> PolicyReply:
                self.session.refresh(row)
                if row.cancel_requested:
                    raise EpisodeCancelled()
                if time.monotonic() - started >= 600:
                    raise RuntimeError("Episode wall budget exhausted")
                return model_decide(observation, timeout)

            with self.environment_type() as env:
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
