"""Tests for skill discovery and internal-over-external precedence."""

from __future__ import annotations

from pathlib import Path

from ml_python_base.skills_sync.discovery import discover_skills
from ml_python_base.skills_sync.models import KIND_EXTERNAL, KIND_INTERNAL


def test_discovery_orders_internal_then_external(repo_root: Path) -> None:
    skills = discover_skills(repo_root)
    kinds = [s.kind for s in skills]
    # All internal skills come before any external skill.
    last_internal = max(
        (i for i, k in enumerate(kinds) if k == KIND_INTERNAL), default=-1
    )
    first_external = next(
        (i for i, k in enumerate(kinds) if k == KIND_EXTERNAL), len(kinds)
    )
    assert last_internal < first_external


def test_discovery_excludes_readme(repo_root: Path) -> None:
    names = {s.name for s in discover_skills(repo_root)}
    assert "README" not in names


def test_internal_skills_are_sorted(repo_root: Path) -> None:
    internal = [s.name for s in discover_skills(repo_root) if s.kind == KIND_INTERNAL]
    assert internal == sorted(internal)


def test_precedence_drops_colliding_external(tmp_path: Path) -> None:
    internal_dir = tmp_path / ".github/skills"
    external_dir = tmp_path / ".github/skills-external"
    internal_dir.mkdir(parents=True)
    (external_dir / "shared").mkdir(parents=True)

    (internal_dir / "shared.md").write_text(
        "---\nname: shared\ndescription: internal one\n---\n", encoding="utf-8"
    )
    (external_dir / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: external one\n---\n", encoding="utf-8"
    )

    skills = discover_skills(tmp_path)
    shared = [s for s in skills if s.name == "shared"]
    assert len(shared) == 1
    assert shared[0].kind == KIND_INTERNAL
    assert shared[0].description == "internal one"
