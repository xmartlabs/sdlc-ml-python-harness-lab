"""Persists the qualitative LLM audit of a run.

The audit itself (an LLM HTTP call to the gateway) runs in the worker via a
subprocess to scripts/bench_audit.py; this service only owns the DB record and
the status lifecycle (running -> done | failed) that the frontend polls.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import Audit, utc_now

if TYPE_CHECKING:
    from pathlib import Path


class AuditService:
    def __init__(self, session: Session):
        self.session = session

    def get_audit(self, run_id: str) -> Audit | None:
        return self.session.exec(select(Audit).where(Audit.run_id == run_id)).first()

    def _audit_path(self, run_id: str) -> Path:
        return get_settings().data_runs_dir / run_id / "audit.md"

    def _upsert(self, run_id: str) -> Audit:
        audit = self.get_audit(run_id)
        if audit is None:
            audit = Audit(run_id=run_id)
            self.session.add(audit)
        return audit

    def mark_running(self, run_id: str, auditor_model: str = "") -> Audit:
        audit = self._upsert(run_id)
        audit.status = "running"
        audit.auditor_model = auditor_model
        audit.error = None
        audit.updated_at = utc_now()
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(audit)
        return audit

    def store_result(
        self, run_id: str, markdown: str, findings: dict | None = None
    ) -> Audit:
        audit = self._upsert(run_id)
        audit.status = "done"
        audit.markdown = markdown
        if findings is not None:
            audit.findings_json = findings
        audit.error = None
        audit.updated_at = utc_now()
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(audit)
        return audit

    def store_error(self, run_id: str, error: str) -> Audit:
        audit = self._upsert(run_id)
        audit.status = "failed"
        audit.error = error
        audit.updated_at = utc_now()
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(audit)
        return audit

    def read_audit_markdown(self, run_id: str) -> str:
        """Read the audit.md the worker's bench_audit subprocess wrote."""
        path = self._audit_path(run_id)
        return path.read_text() if path.exists() else ""

    def read_audit_findings(self, run_id: str) -> dict | None:
        """Read audit_findings.json (structured verdicts), tolerating absence."""
        path = get_settings().data_runs_dir / run_id / "audit_findings.json"
        if not path.exists():
            return None
        try:
            parsed = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"items": [], "parse_error": "unreadable audit_findings.json"}
        return parsed if isinstance(parsed, dict) else None
