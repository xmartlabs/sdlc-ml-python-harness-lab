"""Persists the state of in-platform validation sandboxes (terminal / desktop).

The service owns only the DB record + status lifecycle the frontend polls; the
worker launches/stops the actual container (it owns the docker socket).
"""

from __future__ import annotations

from sqlmodel import Session, select

from harness_lab_app.models import Sandbox, utc_now

# `play` is a desktop that opens the case's own game on start (ADR-0078).
VALID_KINDS = ("terminal", "desktop", "play")


class SandboxService:
    def __init__(self, session: Session):
        self.session = session

    def get_sandbox(self, run_id: str, kind: str) -> Sandbox | None:
        return self.session.exec(
            select(Sandbox).where(Sandbox.run_id == run_id, Sandbox.kind == kind)
        ).first()

    def list_for_run(self, run_id: str) -> list[Sandbox]:
        return list(self.session.exec(select(Sandbox).where(Sandbox.run_id == run_id)))

    def list_active(self) -> list[Sandbox]:
        return list(
            self.session.exec(
                select(Sandbox).where(Sandbox.status.in_(("starting", "running")))  # type: ignore[attr-defined]
            )
        )

    def _upsert(self, run_id: str, kind: str) -> Sandbox:
        sandbox = self.get_sandbox(run_id, kind)
        if sandbox is None:
            sandbox = Sandbox(run_id=run_id, kind=kind)
            self.session.add(sandbox)
        return sandbox

    def mark_starting(self, run_id: str, kind: str) -> Sandbox:
        sandbox = self._upsert(run_id, kind)
        sandbox.status = "starting"
        sandbox.error = None
        sandbox.url = ""
        sandbox.host_port = None
        sandbox.updated_at = utc_now()
        self.session.add(sandbox)
        self.session.commit()
        self.session.refresh(sandbox)
        return sandbox

    def mark_running(
        self, run_id: str, kind: str, container_name: str, host_port: int, url: str
    ) -> Sandbox:
        sandbox = self._upsert(run_id, kind)
        sandbox.status = "running"
        sandbox.container_name = container_name
        sandbox.host_port = host_port
        sandbox.url = url
        sandbox.error = None
        sandbox.updated_at = utc_now()
        self.session.add(sandbox)
        self.session.commit()
        self.session.refresh(sandbox)
        return sandbox

    def mark_stopped(self, run_id: str, kind: str) -> Sandbox:
        sandbox = self._upsert(run_id, kind)
        sandbox.status = "stopped"
        sandbox.url = ""
        sandbox.host_port = None
        sandbox.updated_at = utc_now()
        self.session.add(sandbox)
        self.session.commit()
        self.session.refresh(sandbox)
        return sandbox

    def mark_error(self, run_id: str, kind: str, error: str) -> Sandbox:
        sandbox = self._upsert(run_id, kind)
        sandbox.status = "failed"
        sandbox.error = error
        sandbox.updated_at = utc_now()
        self.session.add(sandbox)
        self.session.commit()
        self.session.refresh(sandbox)
        return sandbox
