"""Lifecycle of staged (experimental) skills: draft → testing → promoted.

A staged skill is authored from the UI and lives in two places kept in sync by
this service: the ``StagedSkill`` DB row (UI source of truth) and
``data/skills_staging/<name>/SKILL.md`` on disk (what the runner injects into a
run via ``--extra-skill``). Promotion writes the skill into
``.github/skills-external/<name>/SKILL.md`` and re-runs the skills_sync engine;
it NEVER commits — reviewing and committing the promoted files is a human step.
"""

from __future__ import annotations

import re
import shutil
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import StagedSkill, utc_now
from harness_lab_app.skills_catalog import known_skill_names

if TYPE_CHECKING:
    from pathlib import Path

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
VALID_STATUSES = {"draft", "testing", "promoted", "archived"}


class StagedSkillError(ValueError):
    """Invalid staged-skill operation (bad name, collision, missing row)."""


class StagedSkillService:
    def __init__(self, session: Session):
        self.session = session
        settings = get_settings()
        self.staging_dir: Path = settings.repo_root / "data" / "skills_staging"
        self.external_dir: Path = settings.repo_root / ".github" / "skills-external"
        self.repo_root: Path = settings.repo_root

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
        if not _NAME_RE.match(normalized):
            raise StagedSkillError(
                "Skill name must be kebab/snake-case: lowercase letters, digits, "
                "'-' or '_', 2-64 chars."
            )
        if not content.strip():
            raise StagedSkillError("SKILL.md content must not be empty.")
        if normalized in known_skill_names():
            raise StagedSkillError(
                f"'{normalized}' collides with a governed skill. Pick another name "
                "(or disable the governed one per-run to A/B a replacement)."
            )
        existing = self.session.exec(
            select(StagedSkill).where(StagedSkill.name == normalized)
        ).first()
        if existing is not None:
            raise StagedSkillError(f"Staged skill already exists: {normalized}")
        skill = StagedSkill(
            name=normalized, description=description.strip(), content=content
        )
        self.session.add(skill)
        self.session.commit()
        self.session.refresh(skill)
        self._write_staging_file(skill)
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
        if content is not None:
            if not content.strip():
                raise StagedSkillError("SKILL.md content must not be empty.")
            skill.content = content
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
        self._write_staging_file(skill)
        return skill

    def delete_skill(self, name: str) -> None:
        skill = self.get_skill(name)
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
        dest = self.external_dir / skill.name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(skill.content, encoding="utf-8")
        self._sync_skill_views()
        skill.status = "promoted"
        skill.updated_at = utc_now()
        self.session.add(skill)
        self.session.commit()
        return {
            "name": skill.name,
            "written": str(dest.relative_to(self.repo_root) / "SKILL.md"),
            "synced": True,
            "next_steps": (
                "Review with `git status`, then commit the new skill and the "
                "regenerated native views yourself (the lab never commits)."
            ),
        }

    # -- internals ----------------------------------------------------------

    def _write_staging_file(self, skill: StagedSkill) -> None:
        target = self.staging_dir / skill.name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(skill.content, encoding="utf-8")

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
