"""Tests for native-view materialization (symlink + copy strategies)."""

from __future__ import annotations

import os
from pathlib import Path

from ml_python_base.skills_sync.discovery import discover_skills
from ml_python_base.skills_sync.linker import link_tool
from ml_python_base.skills_sync.models import ToolSpec


def _seed_repo(root: Path) -> None:
    internal = root / ".github/skills"
    external = root / ".github/skills-external/ext"
    internal.mkdir(parents=True)
    external.mkdir(parents=True)
    (internal / "alpha.md").write_text(
        "---\nname: alpha\ndescription: a\n---\n", encoding="utf-8"
    )
    (internal / "beta.md").write_text(
        "---\nname: beta\ndescription: b\n---\n", encoding="utf-8"
    )
    (internal / "README.md").write_text("index\n", encoding="utf-8")
    (external / "SKILL.md").write_text(
        "---\nname: ext\ndescription: e\n---\n", encoding="utf-8"
    )


def test_symlink_strategy_uses_repo_relative_targets(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    tool = ToolSpec(
        id="claude",
        display_name="Claude Code",
        link_strategy="symlink",
        native_skills_dir=".claude/skills",
    )
    link_tool(tmp_path, tool, discover_skills(tmp_path))

    link = tmp_path / ".claude/skills/alpha/SKILL.md"
    assert link.is_symlink()
    assert os.readlink(link) == "../../../.github/skills/alpha.md"

    ext_link = tmp_path / ".claude/skills/ext/SKILL.md"
    assert os.readlink(ext_link) == "../../../.github/skills-external/ext/SKILL.md"


def test_copy_strategy_writes_files_and_returns_hashes(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    tool = ToolSpec(
        id="antigravity",
        display_name="Antigravity",
        link_strategy="copy",
        native_skills_dir=".agents/skills",
        needs_manifest=True,
        manifest_path=".agents/skills/.generated-manifest.tsv",
    )
    hashes = link_tool(tmp_path, tool, discover_skills(tmp_path))

    copied = tmp_path / ".agents/skills/alpha/SKILL.md"
    assert copied.is_file() and not copied.is_symlink()
    assert copied.read_text(encoding="utf-8").startswith("---")
    # Hash order is internal-first (alpha, beta) then external (ext).
    assert list(hashes.keys()) == ["alpha", "beta", "ext"]


def test_link_removes_stale_skill_dirs(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    tool = ToolSpec(
        id="claude",
        display_name="Claude Code",
        link_strategy="symlink",
        native_skills_dir=".claude/skills",
    )
    stale = tmp_path / ".claude/skills/ghost"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("orphan", encoding="utf-8")

    link_tool(tmp_path, tool, discover_skills(tmp_path))
    assert not stale.exists()
