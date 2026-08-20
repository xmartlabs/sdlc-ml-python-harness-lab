"""Tests for the versioned proposal domain (harness_lifecycle).

Covers Pydantic models, ID allocation, run-evidence extraction with checksums,
atomic YAML round-trips, and idempotent issue linkage. Pure domain logic only —
the LLM synthesis and `gh` wiring live at the CLI edge and are not exercised here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness_lab_app.harness_lifecycle import manifests, proposals
from harness_lab_app.harness_lifecycle.models import (
    Observation,
    Proposal,
    ProposalSource,
    ProposalStatus,
    ProposalValidation,
    SuccessCriterion,
    SuggestedChange,
)

FIXED = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


def _run_dir(root: Path, run_id: str, condition_hash: str = "sha256:abc123") -> Path:
    d = root / "data" / "runs" / run_id
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "case": "systematic-debugging-v1",
                "condition_hash": condition_hash,
            }
        ),
        encoding="utf-8",
    )
    (d / "audit.md").write_text(
        "# Audit\nAgent repeated a failing command.\n", encoding="utf-8"
    )
    return d


def _proposal(proposal_id: str = "HEP-2026-014") -> Proposal:
    return proposals.build_proposal(
        proposal_id=proposal_id,
        title="Improve systematic debugging stop conditions",
        created_at=FIXED,
        source=ProposalSource(
            repository="xmartlabs/sdlc-ml-python-harness-lab", runs=[]
        ),
        observation=Observation(
            failure_mode="Agent repeated an equivalent failing command.",
            expected_behavior="Reframe the hypothesis after the retry budget.",
            evidence_summary="Seen in two independent runs.",
        ),
        affected_artifacts=[".github/skills/systematic_debugging.md"],
        suggested_change=SuggestedChange(
            summary="Add a retry budget and hypothesis reset rule."
        ),
        validation=ProposalValidation(
            suggested_cases=["systematic-debugging-v1"],
            success_criteria=[
                SuccessCriterion(
                    metric="repeated_equivalent_command_max", operator="lte", value=2
                )
            ],
        ),
        confidence="high",
    )


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


def test_proposal_id_validation() -> None:
    p = _proposal("HEP-2026-001")
    assert p.proposal_id == "HEP-2026-001"
    with pytest.raises(ValueError, match="HEP"):
        _proposal("HEP-26-1")


def test_build_proposal_seeds_lifecycle() -> None:
    p = _proposal()
    states = [e.state for e in p.lifecycle]
    assert states == ["observed", "proposed"]
    assert p.status is ProposalStatus.PROPOSED
    assert p.schema_version == 1


# --------------------------------------------------------------------------- #
# YAML round-trip and atomic write
# --------------------------------------------------------------------------- #


def test_yaml_roundtrip() -> None:
    p = _proposal()
    text = manifests.proposal_to_yaml(p)
    assert "proposal_id: HEP-2026-014" in text
    assert "schema_version: 1" in text
    reloaded = manifests.proposal_from_yaml(text)
    assert reloaded == p


def test_write_and_load_proposal(tmp_path: Path) -> None:
    p = _proposal()
    path = proposals.write_proposal(tmp_path, p)
    assert path == tmp_path / "data/harness-proposals/2026/HEP-2026-014.yaml"
    assert path.is_file()
    assert proposals.load_proposal(path) == p


def test_atomic_write_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "file.yaml"
    manifests.atomic_write_text(target, "hello: world\n")
    assert target.read_text() == "hello: world\n"
    # No sibling temp files remain.
    assert [x.name for x in target.parent.iterdir()] == ["file.yaml"]


# --------------------------------------------------------------------------- #
# ID allocation
# --------------------------------------------------------------------------- #


def test_allocate_first_id(tmp_path: Path) -> None:
    assert proposals.allocate_proposal_id(tmp_path, 2026) == "HEP-2026-001"


def test_allocate_next_id(tmp_path: Path) -> None:
    for pid in ("HEP-2026-001", "HEP-2026-002"):
        proposals.write_proposal(tmp_path, _proposal(pid))
    assert proposals.allocate_proposal_id(tmp_path, 2026) == "HEP-2026-003"


def test_allocate_id_is_year_scoped(tmp_path: Path) -> None:
    proposals.write_proposal(tmp_path, _proposal("HEP-2026-005"))
    assert proposals.allocate_proposal_id(tmp_path, 2027) == "HEP-2027-001"


# --------------------------------------------------------------------------- #
# Run-evidence extraction with checksums
# --------------------------------------------------------------------------- #


def test_extract_run_evidence(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, "systematic-debugging-v1-K-003")
    ev = proposals.extract_run_evidence(run_dir)
    assert ev.run_id == "systematic-debugging-v1-K-003"
    assert ev.condition_hash == "sha256:abc123"
    assert ev.artifacts.audit.endswith("audit.md")
    assert ev.artifacts.metadata_sha256.startswith("sha256:")


def test_extract_run_evidence_missing_metadata(tmp_path: Path) -> None:
    d = tmp_path / "data/runs/empty-K-001"
    d.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        proposals.extract_run_evidence(d)


# --------------------------------------------------------------------------- #
# Idempotent issue linkage
# --------------------------------------------------------------------------- #


def test_link_issue_sets_state_and_lifecycle() -> None:
    p = _proposal()
    linked = proposals.link_issue(
        p,
        "https://github.com/xmartlabs/sdlc-ml-python-base/issues/31",
        at=FIXED,
        actor="human",
    )
    assert linked.links.source_issue.endswith("/issues/31")
    assert linked.status is ProposalStatus.ISSUE_CREATED
    assert linked.lifecycle[-1].state == "issue_created"


def test_link_issue_is_idempotent() -> None:
    p = _proposal()
    first = proposals.link_issue(p, "https://x/issues/31", at=FIXED, actor="human")
    second = proposals.link_issue(first, "https://x/issues/99", at=FIXED, actor="human")
    # A second call never rewrites public history.
    assert second.links.source_issue.endswith("/issues/31")
    assert [e.state for e in second.lifecycle].count("issue_created") == 1
