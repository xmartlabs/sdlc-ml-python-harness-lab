from __future__ import annotations

import contextlib
import re
import shutil
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import desc
from sqlmodel import Session, select

from harness_lab_app.config import get_settings, read_env_file_value
from harness_lab_app.models import (
    Artifact,
    Attribution,
    Audit,
    Run,
    RunConfig,
    RunEvent,
    RunState,
    Sandbox,
    Validation,
    utc_now,
)
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.schemas import RunConfigCreate, RunCreate

if TYPE_CHECKING:
    from collections.abc import Callable


# Tokens that mean "no real gateway credential configured yet".
_PLACEHOLDER_TOKENS = {"", "sk-local-change-me"}


class RunAlreadyExistsError(ValueError):
    """Raised when creating a run whose run_id is already in the database."""


class RunPreflightError(ValueError):
    """Raised when a run's config cannot possibly execute (invalid before enqueue)."""


class RunDeleteConflictError(ValueError):
    """Raised when deleting a `running` run without force (cancel it first)."""


def _resume_config(config: RunConfig) -> RunConfigCreate:
    return RunConfigCreate(
        case_id=config.case_id,
        prompt_variant=config.prompt_variant,
        harness=config.harness,
        planner_model=config.planner_model,
        executor_model=config.executor_model,
        score_mode=config.score_mode,
        max_steps=config.max_steps,
        resume=True,
        dry_run=config.dry_run,
    )


class RunService:
    def __init__(
        self,
        session: Session,
        enqueue: Callable[[str], None] | None = None,
        enqueue_cleanup: Callable[[str], None] | None = None,
    ):
        self.session = session
        self.adapter = BenchRunAdapter()
        # Queue publisher injected by the API layer (celery_app.enqueue_run);
        # None (tests, read paths) means runs are created but not published.
        self.enqueue = enqueue
        # Publisher for the worker-only container-cleanup task (delete flow);
        # None means container teardown is skipped (still removes DB + disk).
        self.enqueue_cleanup = enqueue_cleanup

    def _publish(self, run_id: str) -> None:
        if self.enqueue is None:
            return
        try:
            self.enqueue(run_id)
        except Exception as exc:
            # A swallowed enqueue error used to leave the run stuck in `pending`
            # forever with no visible reason. Surface it: mark the run failed so
            # the UI shows why it never started (broker down, etc.).
            self.session.add(
                RunEvent(
                    run_id=run_id,
                    level="error",
                    message=f"Failed to enqueue run (broker unavailable?): {exc!r}",
                )
            )
            run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
            if run is not None and run.state == RunState.pending:
                run.state = RunState.failed
                run.failure_reason = "Queue broker unavailable — run not started"
                run.ended_at = utc_now()
                self.session.add(run)
            self.session.commit()

    def list_runs(self) -> list[Run]:
        statement = select(Run).order_by(desc(cast("Any", Run.created_at)))
        return list(self.session.exec(statement))

    def get_run(self, run_id: str) -> Run:
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    def get_run_config(self, run_config_id: int) -> RunConfig:
        config = self.session.exec(
            select(RunConfig).where(RunConfig.id == run_config_id)
        ).first()
        if config is None:
            raise ValueError(f"Run config not found: {run_config_id}")
        return config

    def preview_command(self, run_id: str, config: RunConfigCreate) -> list[str]:
        return self.adapter.build_command(run_id, config)

    @staticmethod
    def _preflight(config: RunConfigCreate) -> None:
        """Reject configs that cannot possibly run, before persisting/enqueuing.

        Turns the old silent "fails open" behavior (a run that exits 0 having
        built nothing) into an actionable error at creation time (ADR-0006).
        Dry-run needs no model or gateway, so it is exempt.
        """
        if config.dry_run:
            return
        if config.score_mode != "none" and not (config.executor_model or "").strip():
            raise RunPreflightError(
                "Executor model is required for a headless run — pick an executor "
                "model before creating the run."
            )
        executor = (config.executor_model or "").strip()
        if executor.startswith("gateway/"):
            token = read_env_file_value("GATEWAY_TOKEN")
            if token in _PLACEHOLDER_TOKENS:
                raise RunPreflightError(
                    "GATEWAY_TOKEN is not set (still the placeholder value). Set a "
                    "real token in .env before running a gateway/ model."
                )

    def _next_run_id(self, case_id: str) -> str:
        """Return the next free `<case>-K-NNN` id (the web lane, ADR-0006).

        Unique across both the DB and the on-disk run/workspace dirs, so a
        generated run never collides with a stale workspace (the RESUME trap).
        """
        prefix = f"{case_id}-K-"
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_n = 0
        for run in self.session.exec(select(Run)).all():
            match = pattern.match(run.run_id)
            if match:
                max_n = max(max_n, int(match.group(1)))
        settings = get_settings()
        candidate = max_n + 1
        while (settings.data_runs_dir / f"{prefix}{candidate:03d}").exists() or (
            settings.workspace_dir / f"{prefix}{candidate:03d}"
        ).exists():
            candidate += 1
        return f"{prefix}{candidate:03d}"

    def _resolve_run_id(self, data: RunCreate) -> str:
        """Pick the run id: an explicit run_id/label (409 on dup), else generate."""
        explicit = (data.label or data.run_id or "").strip()
        if not explicit:
            return self._next_run_id(data.config.case_id)
        existing = self.session.exec(select(Run).where(Run.run_id == explicit)).first()
        if existing is not None:
            raise RunAlreadyExistsError(
                f"Run '{explicit}' already exists (state: {existing.state}). "
                "Use retry to re-run it, or pick a different run id."
            )
        return explicit

    def create_run(self, data: RunCreate) -> Run:
        self._preflight(data.config)
        run_id = self._resolve_run_id(data)
        config = RunConfig(**data.config.model_dump())
        self.session.add(config)
        self.session.commit()
        self.session.refresh(config)

        command = self.preview_command(run_id, data.config)
        run = Run(
            run_id=run_id,
            run_config_id=config.id or 0,
            state=RunState.pending,
            command=command,
        )
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, message="Run queued"))
        self.session.commit()
        self.session.refresh(run)
        self._publish(run.run_id)
        return run

    def clone_run(self, source_run_id: str) -> Run:
        """Re-queue a prior run's config under a fresh auto-generated id.

        Powers the "Run again" action: same arm, new trial with its own
        workspace. resume is reset to False so it starts clean.
        """
        source = self.get_run(source_run_id)
        config = self.get_run_config(source.run_config_id)
        new_config = RunConfigCreate(
            case_id=config.case_id,
            prompt_variant=config.prompt_variant,
            harness=config.harness,
            planner_model=config.planner_model,
            executor_model=config.executor_model,
            score_mode=config.score_mode,
            max_steps=config.max_steps,
            resume=False,
            dry_run=config.dry_run,
        )
        return self.create_run(RunCreate(config=new_config))

    def mark_running(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        run.state = RunState.running
        run.started_at = utc_now()
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, message="Run started"))
        self.session.commit()
        self.session.refresh(run)
        return run

    def mark_finished(
        self, run_id: str, exit_code: int, failure_reason: str | None = None
    ) -> Run:
        run = self.get_run(run_id)
        run.exit_code = exit_code
        run.failure_reason = failure_reason
        run.ended_at = utc_now()
        run.state = (
            RunState.awaiting_manual_score if exit_code == 0 else RunState.failed
        )
        self.session.add(run)
        self.session.add(
            RunEvent(
                run_id=run_id,
                level="info" if exit_code == 0 else "error",
                message=f"Run exited with {exit_code}",
            )
        )
        self.session.commit()
        self.session.refresh(run)
        return run

    def score_run(
        self, run_id: str, score: int, interventions: int, notes: str = ""
    ) -> Run:
        run = self.get_run(run_id)
        run.score = score
        run.interventions = interventions
        run.notes = notes
        run.state = RunState.completed
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, message=f"Run scored {score}/10"))
        self.session.commit()
        self.session.refresh(run)
        return run

    def mark_cancelled(self, run_id: str, exit_code: int) -> Run:
        run = self.get_run(run_id)
        run.exit_code = exit_code
        run.ended_at = utc_now()
        run.state = RunState.cancelled
        run.cancel_requested = False
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, level="info", message="Run cancelled"))
        self.session.commit()
        self.session.refresh(run)
        return run

    def cancel_run(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        if run.state in {RunState.completed, RunState.failed, RunState.cancelled}:
            return run
        if run.state == RunState.running:
            run.cancel_requested = True
            self.session.add(run)
            self.session.add(
                RunEvent(run_id=run_id, level="info", message="Cancellation requested")
            )
        else:
            run.state = RunState.cancelled
            run.ended_at = utc_now()
            self.session.add(run)
            self.session.add(
                RunEvent(run_id=run_id, level="info", message="Run cancelled")
            )
        self.session.commit()
        self.session.refresh(run)
        return run

    def retry_run(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        config = self.get_run_config(run.run_config_id)
        # Persist resume on the config: the Celery task rebuilds the command
        # from RunConfig at execution time, so the flag must survive there.
        config.resume = True
        self.session.add(config)
        run.command = self.adapter.build_command(
            run_id,
            _resume_config(config),
        )
        run.state = RunState.pending
        run.exit_code = None
        run.failure_reason = None
        run.started_at = None
        run.ended_at = None
        run.cancel_requested = False
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, level="info", message="Run retried"))
        self.session.commit()
        self.session.refresh(run)
        self._publish(run_id)
        return run

    @staticmethod
    def _remove_run_dirs(run_id: str) -> None:
        """Remove on-disk run state, guarded to stay under the repo root.

        Paths are built only from settings + the validated run_id; a run_id that
        resolves outside the repo (e.g. containing ``..``) is refused, so the
        run_id is never treated as a free-text filesystem path (ADR-0007).
        """
        settings = get_settings()
        repo_root = settings.repo_root.resolve()
        for base in (settings.data_runs_dir, settings.workspace_dir):
            target = (base / run_id).resolve()
            if repo_root != target and repo_root not in target.parents:
                continue
            if target.exists():
                shutil.rmtree(target)

    def delete_run(self, run_id: str, *, force: bool = False) -> None:
        """Delete a run and all its state: DB rows, on-disk dirs, container.

        No DB cascades exist, so dependent rows are removed explicitly. A
        `running` run is refused unless force=True (cancel it first). Container
        teardown is enqueued to the worker, which owns the docker socket.
        """
        run = self.get_run(run_id)
        if run.state == RunState.running and not force:
            raise RunDeleteConflictError(
                f"Run '{run_id}' is running. Cancel it first, or delete with force."
            )
        for model in (Validation, Attribution, Audit, Sandbox, Artifact, RunEvent):
            rows = self.session.exec(select(model).where(model.run_id == run_id)).all()
            for row in rows:
                self.session.delete(row)
        config_id = run.run_config_id
        self.session.delete(run)
        config = self.session.exec(
            select(RunConfig).where(RunConfig.id == config_id)
        ).first()
        if config is not None:
            self.session.delete(config)
        self.session.commit()

        self._remove_run_dirs(run_id)

        if self.enqueue_cleanup is not None:
            # Best-effort: a leftover container is harmless once the run is gone.
            with contextlib.suppress(Exception):
                self.enqueue_cleanup(run_id)

    def list_events(self, run_id: str, ascending: bool = False) -> list[RunEvent]:
        order = (
            cast("Any", RunEvent.created_at)
            if ascending
            else desc(cast("Any", RunEvent.created_at))
        )
        statement = select(RunEvent).where(RunEvent.run_id == run_id).order_by(order)
        return list(self.session.exec(statement))
