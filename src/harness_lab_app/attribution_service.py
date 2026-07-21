"""Persists structured harness attribution for a run (pure log parsing)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.attribution import build_attribution, read_step_logs
from harness_lab_app.config import get_settings
from harness_lab_app.models import Attribution, utc_now

if TYPE_CHECKING:
    from pathlib import Path


class AttributionService:
    def __init__(self, session: Session):
        self.session = session

    def get_attribution(self, run_id: str) -> Attribution | None:
        return self.session.exec(
            select(Attribution).where(Attribution.run_id == run_id)
        ).first()

    def _run_dir(self, run_id: str) -> Path:
        return get_settings().data_runs_dir / run_id

    def _skills_available(self, run_dir: Path) -> list[str]:
        try:
            meta = json.loads((run_dir / "metadata.json").read_text())
        except (OSError, json.JSONDecodeError):
            return []
        bundle = meta.get("skills_bundle")
        if isinstance(bundle, list):
            return [str(item) for item in bundle]
        fingerprint = meta.get("harness_fingerprint", {})
        skills = fingerprint.get("skills", {}) if isinstance(fingerprint, dict) else {}
        return sorted(skills) if isinstance(skills, dict) else []

    def index_run(self, run_id: str) -> Attribution:
        run_dir = self._run_dir(run_id)
        log_text, steps = read_step_logs(run_dir)
        data: dict[str, Any] = build_attribution(
            log_text,
            skills_available=self._skills_available(run_dir),
            steps_parsed=steps,
        )

        attribution = self.get_attribution(run_id)
        if attribution is None:
            attribution = Attribution(run_id=run_id)
            self.session.add(attribution)
        attribution.tools = data["tools"]
        attribution.files = {
            "read": data["files_read"],
            "edited": data["files_edited"],
        }
        attribution.bash_commands = data["bash_commands"]
        attribution.agent_modes = data["agent_modes"]
        attribution.harness_usage = data["harness_usage"]
        attribution.summary = data["summary"]
        attribution.steps_parsed = data["steps_parsed"]
        attribution.updated_at = utc_now()
        self.session.add(attribution)
        self.session.commit()
        self.session.refresh(attribution)
        return attribution
