from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from sqlmodel import Session, delete, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import Artifact


class ArtifactService:
    def __init__(self, session: Session, data_runs_dir: Path | None = None):
        self.session = session
        self.data_runs_dir = data_runs_dir or get_settings().data_runs_dir

    def index_run(self, run_id: str) -> list[Artifact]:
        run_dir = self.data_runs_dir / run_id
        artifact_run_id = cast("Any", Artifact.run_id)
        self.session.exec(delete(Artifact).where(artifact_run_id == run_id))
        if not run_dir.exists():
            self.session.commit()
            return []

        artifacts: list[Artifact] = []
        for path in sorted(p for p in run_dir.rglob("*") if p.is_file()):
            artifact = Artifact(
                run_id=run_id,
                name=str(path.relative_to(run_dir)),
                path=str(path),
                media_type=self._media_type(path),
                size_bytes=path.stat().st_size,
            )
            self.session.add(artifact)
            artifacts.append(artifact)
        self.session.commit()
        for artifact in artifacts:
            self.session.refresh(artifact)
        return artifacts

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        statement = (
            select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.name)
        )
        return list(self.session.exec(statement))

    def read_text(self, run_id: str, name: str) -> str:
        artifact_run_id = cast("Any", Artifact.run_id)
        artifact_name = cast("Any", Artifact.name)
        artifact = self.session.exec(
            select(Artifact).where(artifact_run_id == run_id, artifact_name == name)
        ).first()
        if artifact is None:
            raise ValueError(f"Artifact not found: {run_id}/{name}")
        return Path(artifact.path).read_text(errors="replace")

    def _media_type(self, path: Path) -> str:
        if path.suffix == ".json":
            return "application/json"
        if path.suffix in {".md", ".patch", ".log", ".txt"}:
            return "text/plain"
        return "application/octet-stream"
