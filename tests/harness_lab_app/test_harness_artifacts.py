"""Tests for the ground-truth template artifact listing and its fail-closed
validator (regression: issues #43 and #47 shipped hallucinated artifact paths)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from harness_lab_app.harness_lifecycle import artifacts, drafting
from harness_lab_app.harness_lifecycle.models import (
    ProposalSource,
    RunArtifacts,
    RunEvidence,
)

_LISTING = [
    ".github/skills/systematic_debugging.md",
    ".github/standards.md",
    ".opencode/plugin/verify-gate.ts",
    "OPENCODE.md",
    "adapters/templates/opencode.md.j2",
    "opencode.json",
]


def _fake_git(stdout: str, returncode: int = 0):
    class _Result:
        pass

    def runner(args, root, check=True, capture=True):
        result = _Result()
        result.stdout = stdout
        result.returncode = returncode
        return result

    return runner


def test_listing_reads_ls_tree_and_filters_to_governed_surfaces() -> None:
    tree = "\n".join(
        [
            "OPENCODE.md",
            "opencode.json",
            ".github/standards.md",
            ".opencode/plugin/verify-gate.ts",
            "src/ml_python_base/skills_sync/cli.py",  # not a governed surface
            "tests/skills_sync/test_cli.py",  # not a governed surface
            ".github/skills-external/brainstorming.md",  # third-party sync: excluded
            ".claude/skills/verify_changes/SKILL.md",  # generated mirror: excluded
        ]
    )
    listing = artifacts.template_artifact_listing(
        root=None, ref="v9.9.9", runner=_fake_git(tree)
    )
    assert "OPENCODE.md" in listing
    assert "opencode.json" in listing
    assert ".opencode/plugin/verify-gate.ts" in listing
    assert not any(p.startswith(("src/", "tests/")) for p in listing)
    assert not any(p.startswith(".github/skills-external/") for p in listing)
    assert not any(p.startswith(".claude/skills/") for p in listing)


def test_validator_accepts_real_paths_and_is_noop_without_ground_truth() -> None:
    artifacts.validate_affected_artifacts(["OPENCODE.md"], _LISTING)
    artifacts.validate_affected_artifacts(["anything/at/all.md"], [])


def test_validator_rejects_issue_47_style_stale_path_with_nearest_hint() -> None:
    with pytest.raises(artifacts.UnknownArtifactError) as excinfo:
        artifacts.validate_affected_artifacts(["adapters/opencode/AGENTS.md"], _LISTING)
    message = str(excinfo.value)
    assert "adapters/opencode/AGENTS.md" in message
    assert "nearest real path" in message


def test_render_listing_block_names_every_path_and_clips() -> None:
    block = artifacts.render_listing_block(_LISTING)
    assert "Template file listing (ground truth)" in block
    assert "- OPENCODE.md" in block
    clipped = artifacts.render_listing_block(
        [f"path-{i}.md" for i in range(999)], clip=200
    )
    assert "(listing clipped)" in clipped
    assert artifacts.render_listing_block([]) == ""


def _source() -> ProposalSource:
    return ProposalSource(
        repository="owner/template",
        runs=[
            RunEvidence(
                run_id="run-1",
                condition_hash="sha256:" + "0" * 8,
                artifacts=RunArtifacts(audit="audit.md", metadata_sha256="0" * 8),
            )
        ],
    )


def test_draft_to_proposal_fails_closed_on_unknown_artifact() -> None:
    draft = {
        "title": "Harden opencode verification",
        "observation": {
            "failure_mode": "x",
            "expected_behavior": "y",
            "evidence_summary": "z",
        },
        "affected_artifacts": ["adapters/opencode/AGENTS.md"],
        "suggested_change": "do the thing",
    }
    with pytest.raises(artifacts.UnknownArtifactError):
        drafting.draft_to_proposal(
            draft,
            proposal_id="HEP-2026-000",
            created_at=datetime(2026, 8, 18, tzinfo=UTC),
            source=_source(),
            allowed_artifacts=_LISTING,
        )


def test_draft_to_proposal_accepts_listed_artifacts() -> None:
    draft = {
        "title": "Harden opencode verification",
        "observation": {
            "failure_mode": "x",
            "expected_behavior": "y",
            "evidence_summary": "z",
        },
        "affected_artifacts": ["OPENCODE.md", ".github/standards.md"],
        "suggested_change": "do the thing",
    }
    proposal = drafting.draft_to_proposal(
        draft,
        proposal_id="HEP-2026-000",
        created_at=datetime(2026, 8, 18, tzinfo=UTC),
        source=_source(),
        allowed_artifacts=_LISTING,
    )
    assert proposal.affected_artifacts == ["OPENCODE.md", ".github/standards.md"]


def test_group_user_message_carries_listing_block() -> None:
    items = [{"title": "t", "harness": "opencode", "run_id": "r1", "body": "b"}]
    message = drafting.build_group_user_message(items, {}, _LISTING)
    assert "Template file listing (ground truth)" in message
    assert "- OPENCODE.md" in message
