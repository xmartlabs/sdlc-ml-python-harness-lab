"""Tests for adapter region rendering (sentinel splice + idempotency)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ml_python_base.skills_sync.errors import SkillsSyncError
from ml_python_base.skills_sync.models import Governance, Registry, ToolSpec
from ml_python_base.skills_sync.renderer import (
    BEGIN_MARKER,
    END_MARKER,
    render_tool,
)

TEMPLATES = Path("adapters/templates")


def _registry() -> Registry:
    tool = ToolSpec(
        id="demo",
        display_name="Demo",
        link_strategy="symlink",
        native_skills_dir=".demo/skills",
        adapter_file="DEMO.md",
        adapter_template="_skills_block.j2",
    )
    return Registry(
        schema_version=1,
        governance=Governance(files=(), automation="", orchestration=""),
        tools=(tool,),
    )


def _seed(root: Path) -> None:
    (root / TEMPLATES).mkdir(parents=True)
    shared = Path(__file__).resolve().parents[2] / TEMPLATES / "_skills_block.j2"
    (root / TEMPLATES / "_skills_block.j2").write_text(
        shared.read_text(encoding="utf-8"), encoding="utf-8"
    )
    skills = root / ".github/skills"
    skills.mkdir(parents=True)
    (skills / "alpha.md").write_text(
        "---\nname: alpha\ndescription: does alpha\n---\n", encoding="utf-8"
    )
    (root / ".github/skills-external").mkdir(parents=True)
    (root / "DEMO.md").write_text(
        f"# Demo\n\n{BEGIN_MARKER}\n{END_MARKER}\n\n## Tail\n", encoding="utf-8"
    )


def test_render_populates_region_and_is_idempotent(tmp_path: Path) -> None:
    _seed(tmp_path)
    registry = _registry()
    tool = registry.tools[0]

    assert render_tool(tmp_path, registry, tool) is True
    body = (tmp_path / "DEMO.md").read_text(encoding="utf-8")
    assert "- `alpha` — does alpha" in body
    assert body.count(BEGIN_MARKER) == 1
    assert "## Tail" in body  # surrounding prose preserved

    # Second render makes no change.
    assert render_tool(tmp_path, registry, tool) is False


def test_render_requires_sentinels(tmp_path: Path) -> None:
    _seed(tmp_path)
    (tmp_path / "DEMO.md").write_text("# Demo\n\nno markers here\n", encoding="utf-8")
    registry = _registry()
    with pytest.raises(SkillsSyncError):
        render_tool(tmp_path, registry, registry.tools[0])
