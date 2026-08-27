"""Database-facing glue for the Langfuse exporter.

The `observability` package is deliberately pure: it reads `data/runs/<id>/`
and returns dictionaries. Everything the lab knows about a run that is *not* on
disk — its state, exit code, score, and objective validation — lives in the
database, and this service is the one place those two halves meet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlmodel import select

from harness_lab_app.config import get_settings
from harness_lab_app.models import Run, RunEvent, Validation
from harness_lab_app.observability.config import LangfuseConfig
from harness_lab_app.observability.run_export import ExportResult, export_run

if TYPE_CHECKING:
    from pathlib import Path

    from sqlmodel import Session


class ObservabilityService:
    """Export one run's traces, enriched with what only the database knows."""

    def __init__(self, session: Session, config: LangfuseConfig | None = None):
        self.session = session
        # Resolved per instance rather than cached at import time so that
        # editing the keys in Settings takes effect without a restart.
        self.config = config or LangfuseConfig.from_env()

    def run_dir(self, run_id: str) -> Path:
        return get_settings().data_runs_dir / run_id

    def run_extra(self, run_id: str) -> dict[str, Any]:
        """Run facts that never reach `data/runs/<id>/`."""
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        extra: dict[str, Any] = {"run_id": run_id}
        if run is not None:
            extra.update(
                {
                    "state": getattr(run.state, "value", run.state),
                    "exit_code": run.exit_code,
                    "failure_reason": run.failure_reason,
                    "score": run.score,
                    "interventions": run.interventions,
                    "parent_run_id": run.parent_run_id,
                    "batch_id": run.batch_id,
                }
            )
        validation = self.session.exec(
            select(Validation).where(Validation.run_id == run_id)
        ).first()
        if validation is not None:
            extra["validation"] = {
                "objective": validation.objective,
                "cost": validation.cost,
                "effort": validation.effort,
                "subjective": validation.subjective,
                "composite": validation.composite,
            }
        return {key: value for key, value in extra.items() if value is not None}

    def export(self, run_id: str) -> ExportResult:
        """Send the run's traces. Never raises; the result explains any no-op."""
        return export_run(
            self.run_dir(run_id),
            config=self.config,
            extra=self.run_extra(run_id),
        )

    def status(self, run_id: str) -> dict[str, Any]:
        """What the run detail page shows about tracing for this run."""
        return {
            "enabled": self.config.enabled,
            "reason": self.config.disabled_reason,
            "session_id": self.config.session_id(run_id),
            "session_url": self.config.session_url(run_id),
            "auto_export": self.config.auto_export,
        }


def export_run_traces(session: Session, run_id: str) -> ExportResult | None:
    """Fire-and-forget export used by the worker at the end of a run.

    Returns None when auto-export is off. Failures are recorded as a run event
    and swallowed: observability degrading must never change a run's outcome.
    """
    service = ObservabilityService(session)
    if not service.config.enabled or not service.config.auto_export:
        return None
    try:
        result = service.export(run_id)
    except Exception as exc:  # the exporter already fails open; belt and braces
        session.add(
            RunEvent(
                run_id=run_id,
                level="error",
                message=f"Langfuse export failed: {exc!r}",
            )
        )
        session.commit()
        return None
    message = (
        f"Langfuse: {result.spans} spans in session {result.session_id}"
        if result.ok
        else f"Langfuse export skipped: {result.reason}"
    )
    session.add(
        RunEvent(
            run_id=run_id,
            level="info" if result.ok else "error",
            message=message[:500],
        )
    )
    session.commit()
    return result
