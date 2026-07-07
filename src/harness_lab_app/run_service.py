from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import desc
from sqlmodel import Session, select

from harness_lab_app.models import Run, RunConfig, RunEvent, RunState, utc_now
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.schemas import RunConfigCreate

if TYPE_CHECKING:
    from harness_lab_app.schemas import RunCreate


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
    def __init__(self, session: Session):
        self.session = session
        self.adapter = BenchRunAdapter()

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

    def create_run(self, data: RunCreate) -> Run:
        config = RunConfig(**data.config.model_dump())
        self.session.add(config)
        self.session.commit()
        self.session.refresh(config)

        command = self.preview_command(data.run_id, data.config)
        run = Run(
            run_id=data.run_id,
            run_config_id=config.id or 0,
            state=RunState.pending,
            command=command,
        )
        self.session.add(run)
        self.session.add(RunEvent(run_id=data.run_id, message="Run queued"))
        self.session.commit()
        self.session.refresh(run)
        return run

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
        return run

    def list_events(self, run_id: str, ascending: bool = False) -> list[RunEvent]:
        order = (
            cast("Any", RunEvent.created_at)
            if ascending
            else desc(cast("Any", RunEvent.created_at))
        )
        statement = select(RunEvent).where(RunEvent.run_id == run_id).order_by(order)
        return list(self.session.exec(statement))
