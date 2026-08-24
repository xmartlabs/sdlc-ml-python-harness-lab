from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sqlmodel import Session, delete, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import Artifact

if TYPE_CHECKING:
    from collections.abc import Iterator

# Run subtrees that are never indexed as artifacts.
#
# `workspace/` is the agent's checkout — for a cloned repository that is tens of
# thousands of files, and indexing one openlibrary run wrote 96,504 of the
# 723,536 rows in the artifact table. Nothing reads them: the artifacts panel
# filters `workspace/` out of its list and points at the Terminal tab, and every
# other consumer (validation, audit, attribution) opens files by path instead.
# `sandbox-*` holds the throwaway copy a sandbox container runs from, which is
# deleted with the container.
#
# Skipping them is what keeps a run's index the handful of files it is meant to
# be — metadata.json, prompt.md, report.md, audit.md, diff.patch, steps/.
EXCLUDED_ARTIFACT_DIRS = ("workspace",)
EXCLUDED_ARTIFACT_DIR_PREFIXES = ("sandbox-",)


def is_indexable(relative_path: Path) -> bool:
    """Whether a run-relative path belongs in the artifact index."""
    top = relative_path.parts[0] if relative_path.parts else ""
    if top in EXCLUDED_ARTIFACT_DIRS:
        return False
    return not top.startswith(EXCLUDED_ARTIFACT_DIR_PREFIXES)


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
        for path in sorted(self._indexable_files(run_dir)):
            name = path.relative_to(run_dir)
            artifact = Artifact(
                run_id=run_id,
                name=str(name),
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

    @staticmethod
    def _indexable_files(run_dir: Path) -> Iterator[Path]:
        """Every file under ``run_dir`` that belongs in the index.

        Excluded directories are skipped before descending into them: walking a
        cloned repository only to discard it costs the same minutes as indexing
        it did.
        """
        for entry in run_dir.iterdir():
            if entry.is_file():
                yield entry
            elif entry.is_dir() and is_indexable(Path(entry.name)):
                yield from (path for path in entry.rglob("*") if path.is_file())

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
