"""Tests for governed agent discovery and native projection."""

from __future__ import annotations

from pathlib import Path

from ml_python_base.skills_sync.agents import (
    discover_agents,
    project_agents,
    render_agent,
)
from ml_python_base.skills_sync.models import ToolSpec

REPO_ROOT = Path(__file__).resolve().parents[2]


def _claude_tool() -> ToolSpec:
    return ToolSpec(
        id="claude",
        display_name="Claude Code",
        link_strategy="symlink",
        native_agents_dir=".claude/agents",
        agent_format="claude",
    )


def _opencode_tool() -> ToolSpec:
    return ToolSpec(
        id="opencode",
        display_name="OpenCode",
        link_strategy="symlink",
        native_agents_dir=".opencode/agents",
        agent_format="opencode",
    )


def test_discovers_governed_agents() -> None:
    agents = {a.name for a in discover_agents(REPO_ROOT)}
    assert {"orchestrator", "planner", "implementer", "tester"} <= agents
    assert "README" not in agents


def test_orchestrator_parsed_with_lists() -> None:
    agents = {a.name: a for a in discover_agents(REPO_ROOT)}
    orch = agents["orchestrator"]
    assert orch.is_orchestrator
    assert orch.mode == "primary"
    assert "task" in orch.allowed_tools
    assert "planner" in orch.delegates_to


def test_render_claude_maps_tools_and_adds_skill() -> None:
    agents = {a.name: a for a in discover_agents(REPO_ROOT)}
    out = render_agent(_claude_tool(), agents["implementer"])
    assert out.startswith("---\nname: implementer\n")
    # Agnostic tools mapped to Claude names; Skill added because skills are bound.
    assert "tools: Read, Grep, Edit, Bash" in out
    assert "Skill" in out
    assert "Runtime model mapping: see `.github/portability.md`." in out


def test_render_claude_assigns_model_by_tier() -> None:
    # planner tier -> opus (flagship for planning/review); executor -> sonnet
    # (cheaper execution); fast -> haiku. Aliases, not pinned ids.
    agents = {a.name: a for a in discover_agents(REPO_ROOT)}
    assert "model: opus" in render_agent(_claude_tool(), agents["planner"])
    assert "model: sonnet" in render_agent(_claude_tool(), agents["implementer"])
    assert "model: haiku" in render_agent(_claude_tool(), agents["documenter"])


def test_render_opencode_uses_mode() -> None:
    agents = {a.name: a for a in discover_agents(REPO_ROOT)}
    out = render_agent(_opencode_tool(), agents["tester"])
    assert "mode: subagent" in out
    assert out.startswith("---\ndescription:")


def test_render_opencode_permission_is_read_only_for_planner() -> None:
    # planner declares allowed_tools [read, grep] -> edit/bash/task must be denied,
    # so OpenCode genuinely cannot let it mutate the tree.
    agents = {a.name: a for a in discover_agents(REPO_ROOT)}
    out = render_agent(_opencode_tool(), agents["planner"])
    assert "permission:" in out
    assert "read: allow" in out
    assert "grep: allow" in out
    assert "edit: deny" in out
    assert "bash: deny" in out


def test_render_opencode_permission_allows_implementer_to_edit() -> None:
    agents = {a.name: a for a in discover_agents(REPO_ROOT)}
    out = render_agent(_opencode_tool(), agents["implementer"])
    assert "edit: allow" in out
    assert "bash: allow" in out
    assert "task: deny" in out  # worker, not an orchestrator


def test_projection_writes_and_prunes(tmp_path: Path) -> None:
    src = tmp_path / ".github/agents"
    src.mkdir(parents=True)
    (src / "solo.md").write_text(
        "---\nname: solo\ndescription: d\nkind: worker\nmode: subagent\n"
        "tier: fast\nallowed_tools: [read]\ngovernance: [standards]\n"
        "skills: []\ndelegates_to: []\ncontext_budget: small\n---\nbody\n",
        encoding="utf-8",
    )
    tool = _claude_tool()
    agents = discover_agents(tmp_path)

    written = project_agents(tmp_path, tool, agents)
    assert written == 1
    assert (tmp_path / ".claude/agents/solo.md").is_file()

    # A stale agent file is pruned on the next projection.
    stale = tmp_path / ".claude/agents/ghost.md"
    stale.write_text("old", encoding="utf-8")
    project_agents(tmp_path, tool, discover_agents(tmp_path))
    assert not stale.exists()
