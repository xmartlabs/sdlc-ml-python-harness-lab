"""Lifecycle of staged (experimental) skills: draft → testing → promoted.

A staged skill is authored or imported from the UI and lives in two places kept
in sync by this service: the DB (``StagedSkill`` plus one ``StagedSkillFile``
per file — the source of truth) and ``data/skills_staging/<name>/`` on disk,
which is a *rebuildable materialization* the runner injects into a run via
``--extra-skill``.

That direction matters (ADR-0017): a real skill ships ``scripts/`` and
``references/`` alongside SKILL.md, and wiping ``data/`` or cloning fresh must
not lose them. :meth:`StagedSkillService.reconcile_all` rebuilds the whole
staging tree from the DB, and is what the health panel's heal action calls.

Promotion copies the whole tree into ``.github/skills-external/<name>/`` and
re-runs the skills_sync engine; it NEVER commits — reviewing and committing the
promoted files is a human step.
"""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import StagedSkill, StagedSkillFile, utc_now
from harness_lab_app.skill_import import (
    SKILL_FILE,
    ImportedSkill,
    resolve_within,
    safe_relpath,
)
from harness_lab_app.skills_catalog import known_skill_names
from ml_python_base.skills_sync.hashing import folder_hash

if TYPE_CHECKING:
    from pathlib import Path

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
VALID_STATUSES = {"draft", "testing", "promoted", "archived"}
VALID_SOURCE_KINDS = {"manual", "url", "archive", "git", "upload"}


class StagedSkillError(ValueError):
    """Invalid staged-skill operation (bad name, collision, missing row)."""


def _decode(row: StagedSkillFile) -> bytes:
    """Bytes of a stored file, whichever encoding it landed in."""
    if row.encoding == "base64":
        return base64.b64decode(row.content)
    return row.content.encode("utf-8")


class StagedSkillService:
    def __init__(self, session: Session):
        self.session = session
        settings = get_settings()
        self.staging_dir: Path = settings.repo_root / "data" / "skills_staging"
        self.external_dir: Path = settings.repo_root / ".github" / "skills-external"
        self.repo_root: Path = settings.repo_root

    # -- file storage -------------------------------------------------------

    def list_files(self, name: str) -> list[StagedSkillFile]:
        skill = self.get_skill(name)
        statement = (
            select(StagedSkillFile)
            .where(StagedSkillFile.staged_skill_id == skill.id)
            .order_by(StagedSkillFile.path)
        )
        return list(self.session.exec(statement))

    def get_file(self, name: str, path: str) -> StagedSkillFile:
        skill = self.get_skill(name)
        row = self.session.exec(
            select(StagedSkillFile)
            .where(StagedSkillFile.staged_skill_id == skill.id)
            .where(StagedSkillFile.path == path)
        ).first()
        if row is None:
            raise StagedSkillError(f"File not found in '{name}': {path}")
        return row

    def put_file(self, name: str, path: str, content: str) -> StagedSkillFile:
        """Create or replace one text file of the tree."""
        skill = self.get_skill(name)
        relative = safe_relpath(path)
        if relative is None:
            raise StagedSkillError(f"Unsafe file path: {path}")
        if relative == SKILL_FILE and not content.strip():
            raise StagedSkillError("SKILL.md content must not be empty.")
        files = self._read_files(skill)
        files[relative] = content.encode("utf-8")
        self._store_files(skill, files)
        return self.get_file(name, relative)

    def delete_file(self, name: str, path: str) -> None:
        skill = self.get_skill(name)
        if path == SKILL_FILE:
            raise StagedSkillError("SKILL.md cannot be deleted; delete the skill.")
        files = self._read_files(skill)
        if path not in files:
            raise StagedSkillError(f"File not found in '{name}': {path}")
        del files[path]
        self._store_files(skill, files)

    # -- import / materialization -------------------------------------------

    def import_skill(
        self, imported: ImportedSkill, name: str = "", replace: bool = False
    ) -> StagedSkill:
        """Persist a fetched tree as a staged skill.

        ``replace`` is opt-in on purpose: re-importing is how a user picks up
        an upstream fix, but silently overwriting local edits would lose work.
        """
        normalized = (name or imported.name).strip().lower()
        self._assert_name_available(normalized, allow_existing=replace)
        if imported.source_kind not in VALID_SOURCE_KINDS:
            raise StagedSkillError(f"Unknown source kind: {imported.source_kind}")

        skill = self.session.exec(
            select(StagedSkill).where(StagedSkill.name == normalized)
        ).first()
        if skill is None:
            skill = StagedSkill(name=normalized)
            self.session.add(skill)
        if imported.description:
            skill.description = imported.description[:500]
        skill.source_url = imported.source_url
        skill.source_ref = imported.source_ref
        skill.source_kind = imported.source_kind
        skill.imported_at = utc_now()
        skill.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(skill)
        self._store_files(skill, dict(imported.files))
        return skill

    def materialize(self, skill: StagedSkill) -> Path:
        """Write the whole tree to staging and prune anything no longer in the DB.

        Pruning is what keeps an A/B honest: a file deleted in the UI must stop
        reaching the runner, not linger from a previous materialization.
        """
        target = self.staging_dir / skill.name
        target.mkdir(parents=True, exist_ok=True)
        wanted: set[Path] = set()
        for row in self.list_files(skill.name):
            destination = resolve_within(target, row.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(_decode(row))
            wanted.add(destination)

        for existing in sorted(target.rglob("*"), reverse=True):
            if existing.is_symlink() or existing.is_file():
                if existing.resolve() not in wanted:
                    existing.unlink()
            elif existing.is_dir() and not any(existing.iterdir()):
                existing.rmdir()
        return target

    def reconcile_all(self) -> dict:
        """Rebuild every non-archived skill's staging tree from the DB (heal)."""
        rebuilt: list[str] = []
        for skill in self.list_skills():
            if skill.status == "archived":
                continue
            self.materialize(skill)
            rebuilt.append(skill.name)

        return {"rebuilt": rebuilt, "orphans": self.staging_orphans()}

    def staging_orphans(self) -> list[str]:
        """Staging directories with no DB row — leftovers from a deleted skill.

        The runner would happily inject one of these into a run, so they are
        drift to report, not history to keep.
        """
        known = {skill.name for skill in self.list_skills()}
        if not self.staging_dir.is_dir():
            return []
        return [
            entry.name
            for entry in sorted(self.staging_dir.iterdir())
            if entry.is_dir() and entry.name not in known
        ]

    def staging_drift(self) -> list[str]:
        """Paths where disk disagrees with the DB (the health panel's signal)."""
        stale: list[str] = []
        for skill in self.list_skills():
            if skill.status == "archived":
                continue
            target = self.staging_dir / skill.name
            if not target.is_dir():
                stale.append(f"data/skills_staging/{skill.name} (missing)")
                continue
            if skill.content_hash and folder_hash(target) != skill.content_hash:
                stale.append(f"data/skills_staging/{skill.name} (out of date)")
        return stale

    # -- queries ------------------------------------------------------------

    def list_skills(self) -> list[StagedSkill]:
        statement = select(StagedSkill).order_by(StagedSkill.name)
        return list(self.session.exec(statement))

    def get_skill(self, name: str) -> StagedSkill:
        skill = self.session.exec(
            select(StagedSkill).where(StagedSkill.name == name)
        ).first()
        if skill is None:
            raise StagedSkillError(f"Staged skill not found: {name}")
        return skill

    def active_names(self) -> set[str]:
        """Names usable as --extra-skill (anything not archived)."""
        return {
            skill.name for skill in self.list_skills() if skill.status != "archived"
        }

    # -- lifecycle ----------------------------------------------------------

    def create_skill(self, name: str, description: str, content: str) -> StagedSkill:
        normalized = name.strip().lower()
        self._assert_name_available(normalized)
        if not content.strip():
            raise StagedSkillError("SKILL.md content must not be empty.")
        skill = StagedSkill(
            name=normalized,
            description=description.strip(),
            content=content,
            source_kind="manual",
        )
        self.session.add(skill)
        self.session.commit()
        self.session.refresh(skill)
        self._store_files(skill, {SKILL_FILE: content.encode("utf-8")})
        return skill

    def update_skill(
        self,
        name: str,
        description: str | None = None,
        content: str | None = None,
        status: str | None = None,
    ) -> StagedSkill:
        skill = self.get_skill(name)
        if description is not None:
            skill.description = description.strip()
        if status is not None:
            if status not in VALID_STATUSES:
                raise StagedSkillError(
                    f"Invalid status '{status}'. Valid: {sorted(VALID_STATUSES)}"
                )
            skill.status = status
        skill.updated_at = utc_now()
        self.session.add(skill)
        self.session.commit()
        self.session.refresh(skill)

        # `content` edits go through the file store so the tree, the mirror
        # column and content_hash can never disagree.
        if content is not None:
            if not content.strip():
                raise StagedSkillError("SKILL.md content must not be empty.")
            files = self._read_files(skill)
            files[SKILL_FILE] = content.encode("utf-8")
            self._store_files(skill, files)
        else:
            self.materialize(skill)
        return skill

    def delete_skill(self, name: str) -> None:
        skill = self.get_skill(name)
        for row in self.list_files(name):
            self.session.delete(row)
        self.session.delete(skill)
        self.session.commit()
        target = self.staging_dir / name
        if target.is_dir():
            shutil.rmtree(target)

    def promote_skill(self, name: str) -> dict:
        """Write the skill into the governed external catalog and re-sync.

        Returns the list of files/dirs the caller should review and commit —
        this service never runs git.
        """
        skill = self.get_skill(name)
        if name in known_skill_names():
            raise StagedSkillError(f"'{name}' already exists in the governed catalog.")
        rows = self.list_files(name)
        if not rows:
            raise StagedSkillError(f"'{name}' has no files to promote.")
        dest = self.external_dir / skill.name
        dest.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for row in rows:
            target = resolve_within(dest, row.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_decode(row))
            written.append(str(target.relative_to(self.repo_root)))
        self._sync_skill_views()
        skill.status = "promoted"
        skill.updated_at = utc_now()
        self.session.add(skill)
        self.session.commit()
        return {
            "name": skill.name,
            "written": written,
            "synced": True,
            "next_steps": (
                "Review with `git status`, then commit the new skill and the "
                "regenerated native views yourself (the lab never commits)."
            ),
        }

    # -- internals ----------------------------------------------------------

    def _assert_name_available(self, name: str, allow_existing: bool = False) -> None:
        if not _NAME_RE.match(name):
            raise StagedSkillError(
                "Skill name must be kebab/snake-case: lowercase letters, digits, "
                "'-' or '_', 2-64 chars."
            )
        if name in known_skill_names():
            raise StagedSkillError(
                f"'{name}' collides with a governed skill. Pick another name "
                "(or disable the governed one per-run to A/B a replacement)."
            )
        if allow_existing:
            return
        existing = self.session.exec(
            select(StagedSkill).where(StagedSkill.name == name)
        ).first()
        if existing is not None:
            raise StagedSkillError(f"Staged skill already exists: {name}")

    def _read_files(self, skill: StagedSkill) -> dict[str, bytes]:
        return {row.path: _decode(row) for row in self.list_files(skill.name)}

    def _store_files(self, skill: StagedSkill, files: dict[str, bytes]) -> None:
        """Replace the whole file set, then re-materialize and re-hash.

        Replacing wholesale rather than diffing keeps this one short path: the
        rows, the ``content`` mirror, the staging tree and ``content_hash`` are
        all derived from the same dict in the same transaction.
        """
        if SKILL_FILE not in files:
            raise StagedSkillError("A skill must contain a SKILL.md file.")

        normalized: dict[str, bytes] = {}
        for path, blob in files.items():
            relative = safe_relpath(path)
            if relative is None:
                raise StagedSkillError(f"Unsafe file path: {path}")
            normalized[relative] = blob

        for row in self.list_files(skill.name):
            self.session.delete(row)
        self.session.flush()

        now = utc_now()
        for path in sorted(normalized):
            blob = normalized[path]
            try:
                text, encoding = blob.decode("utf-8"), "utf-8"
            except UnicodeDecodeError:
                text, encoding = base64.b64encode(blob).decode("ascii"), "base64"
            self.session.add(
                StagedSkillFile(
                    staged_skill_id=skill.id,
                    path=path,
                    content=text,
                    encoding=encoding,
                    sha256=hashlib.sha256(blob).hexdigest(),
                    size=len(blob),
                    created_at=now,
                    updated_at=now,
                )
            )

        skill.content = normalized[SKILL_FILE].decode("utf-8", "replace")
        skill.updated_at = now
        self.session.add(skill)
        self.session.commit()
        self.session.refresh(skill)

        target = self.materialize(skill)
        skill.content_hash = folder_hash(target)
        self.session.add(skill)
        self.session.commit()
        self.session.refresh(skill)

    def _sync_skill_views(self) -> None:
        """Equivalent of `make sync-skills` via the engine's own CLI entrypoint."""
        from ml_python_base.skills_sync.cli import main as skills_sync_main

        code = skills_sync_main(
            [
                "--root",
                str(self.repo_root),
                "--registry",
                str(self.repo_root / "adapters" / "registry.toml"),
                "sync",
            ]
        )
        if code != 0:
            raise StagedSkillError(f"skills_sync sync failed with exit code {code}")
