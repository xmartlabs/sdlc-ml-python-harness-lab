"""The candidate skill a case ships, and staging it for a trial (ADR-0087).

A case built to trial a skill ships the candidate next to its grader, under
``benchmarks/<case>/candidate-skill/<name>/``, where ``agent_hidden`` keeps it
out of every agent container: a control arm that could read the candidate would
not be a control. The treatment arm receives it the way every new skill does —
as a **staged** skill (ADR-0011/0017).

This module is shared by ``make stage-candidate`` and the web UI (ADR-0091), so
both stage exactly the same tree. Nothing in ``.github/`` changes; promotion
stays a human action once a trial has earned it (ADR-0086).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_lab_app.skill_import import build_skill
from harness_lab_app.staged_skill_service import StagedSkillService

if TYPE_CHECKING:
    from pathlib import Path

    from sqlmodel import Session

CANDIDATE_GLOB = "benchmarks/*/candidate-skill/*/SKILL.md"
SOURCE_KIND = "manual"


class CandidateSkillError(ValueError):
    """A candidate that cannot be found or staged as asked."""


@dataclass(frozen=True)
class StageResult:
    name: str
    files: int
    source: str
    warnings: list[str] = field(default_factory=list)


def candidates(root: Path) -> dict[str, Path]:
    """Every candidate skill in the case corpus, by folder name."""
    found: dict[str, Path] = {}
    for skill_md in sorted(root.glob(CANDIDATE_GLOB)):
        name = skill_md.parent.name
        if name in found:
            raise CandidateSkillError(
                f"candidate '{name}' is declared twice: {found[name]} and "
                f"{skill_md.parent}"
            )
        found[name] = skill_md.parent
    return found


def candidates_for_case(root: Path, case_id: str) -> dict[str, Path]:
    """The candidates one case ships (``benchmarks/<case_id>/candidate-skill/``)."""
    folder = root / "benchmarks" / case_id / "candidate-skill"
    if not folder.is_dir():
        return {}
    return {
        skill_md.parent.name: skill_md.parent
        for skill_md in sorted(folder.glob("*/SKILL.md"))
    }


def read_tree(folder: Path) -> dict[str, bytes]:
    """The candidate's files, relative to its folder; symlinks are skipped."""
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
    }


def stage(
    session: Session, root: Path, folder: Path, name: str, *, replace: bool = False
) -> StageResult:
    """Store the candidate's whole tree as a staged skill.

    Raises ``SkillImportError``/``StagedSkillError`` from the shared services
    unchanged, and ``CandidateSkillError`` when SKILL.md names another skill.
    """
    imported = build_skill(
        read_tree(folder),
        fallback_name=name,
        source_url=str(folder.relative_to(root)),
        source_kind=SOURCE_KIND,
    )
    if imported.name != name:
        raise CandidateSkillError(
            f"SKILL.md names '{imported.name}' but its folder is '{name}'; "
            "make them match."
        )
    skill = StagedSkillService(session).import_skill(
        imported, name=name, replace=replace
    )
    return StageResult(
        name=skill.name,
        files=len(imported.files),
        source=str(folder.relative_to(root)),
        warnings=list(imported.warnings),
    )
