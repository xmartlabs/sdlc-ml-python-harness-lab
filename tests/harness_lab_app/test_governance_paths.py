"""Where a harness lives: the path predicates every reader shares."""

from __future__ import annotations

import pytest

from harness_lab_app import governance_paths as gp


@pytest.mark.parametrize(
    ("path", "is_skill"),
    [
        (".claude/skills/plan/SKILL.md", True),
        ("skills/talos-admit/SKILL.md", True),
        ("plugins/tribunal/skills/review-pr/SKILL.md", True),
        # Nested one more level, as a skill folder can be.
        ("plugins/tribunal/skills/group/review-pr/SKILL.md", True),
        # Documentation beside the skills is not a skill.
        ("plugins/tribunal/skills/README.md", False),
        # A SKILL.md outside a skills/ slot is not a plugin skill.
        ("plugins/tribunal/docs/SKILL.md", False),
        ("plugins/SKILL.md", False),
        ("src/plugins/x/skills/y/SKILL.md", False),
    ],
)
def test_a_plugin_skill_is_recognised_by_its_slot(path, is_skill):
    assert gp.is_skill_path(path) is is_skill


@pytest.mark.parametrize(
    ("path", "is_agent"),
    [
        (".claude/agents/review/deep.md", True),
        ("plugins/tribunal/agents/tribunal-refuter.md", True),
        ("plugins/tribunal/agents/review/deep.md", True),
        ("plugins/tribunal/agents/notes.txt", False),
        ("plugins/tribunal/methodology/code-review.md", False),
        # A bare root agents/ stays out, plugins or not (see the module note).
        ("agents/reviewer.md", False),
    ],
)
def test_a_plugin_agent_is_recognised_by_its_slot(path, is_agent):
    assert gp.is_agent_path(path) is is_agent


def test_plugin_components_are_named_as_claude_code_invokes_them():
    """`<plugin>:<name>` also keeps two plugins' same-named skills apart."""
    assert gp.skill_name("plugins/tribunal/skills/review-pr/SKILL.md") == (
        "tribunal:review-pr"
    )
    assert gp.skill_name(".claude/skills/plan/SKILL.md") == "plan"
    assert gp.agent_name("plugins/tribunal/agents/tribunal-refuter.md") == (
        "tribunal:tribunal-refuter"
    )
    assert gp.agent_name(".claude/agents/review/deep.md") == "review/deep"
