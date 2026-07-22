"""Versioned proposal domain: IDs, evidence, storage, and issue linkage.

Reviewed proposals live under a tracked ``data/harness-proposals/<year>/`` tree
(distinct from the git-ignored ``data/runs/``). IDs are ``HEP-YYYY-NNN`` allocated
per year. All writes are atomic. Issue linkage is idempotent so public history is
never silently rewritten (plan §6.1).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from harness_lab_app.harness_lifecycle import manifests
from harness_lab_app.harness_lifecycle.models import (
    Confidence,
    LifecycleEvent,
    Observation,
    Proposal,
    ProposalSource,
    ProposalStatus,
    ProposalValidation,
    RunArtifacts,
    RunEvidence,
    SuggestedChange,
)
from harness_lab_app.provenance import hash_file

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

PROPOSALS_SUBDIR = "data/harness-proposals"
RUNS_SUBDIR = "data/runs"
_ID_NUMBER_RE = re.compile(r"^HEP-(\d{4})-(\d{3})$")


def proposals_dir(root: Path) -> Path:
    return root / PROPOSALS_SUBDIR


def year_dir(root: Path, year: int) -> Path:
    return proposals_dir(root) / str(year)


def _year_of(proposal_id: str) -> int:
    match = _ID_NUMBER_RE.match(proposal_id)
    if not match:
        raise ValueError(f"Invalid proposal_id '{proposal_id}'.")
    return int(match.group(1))


def proposal_path(root: Path, proposal_id: str) -> Path:
    return year_dir(root, _year_of(proposal_id)) / f"{proposal_id}.yaml"


def allocate_proposal_id(root: Path, year: int) -> str:
    """Return the next unused ``HEP-<year>-NNN`` id (scan-based, per year)."""
    directory = year_dir(root, year)
    highest = 0
    if directory.is_dir():
        for path in directory.glob(f"HEP-{year}-*.yaml"):
            match = _ID_NUMBER_RE.match(path.stem)
            if match and int(match.group(1)) == year:
                highest = max(highest, int(match.group(2)))
    return f"HEP-{year}-{highest + 1:03d}"


def extract_run_evidence(run_dir: Path) -> RunEvidence:
    """Read a run's metadata and hash its evidence artifacts.

    Raises ``FileNotFoundError`` if the run has no ``metadata.json``. The owning
    repository is recorded separately by the caller on the proposal source.
    """
    metadata_path = run_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"No metadata.json in {run_dir}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    run_id = str(metadata.get("run_id") or run_dir.name)
    condition_hash = str(metadata.get("condition_hash", ""))
    metadata_sha256 = hash_file(metadata_path) or ""
    return RunEvidence(
        run_id=run_id,
        condition_hash=condition_hash,
        artifacts=RunArtifacts(
            audit=f"{RUNS_SUBDIR}/{run_id}/audit.md",
            metadata_sha256=metadata_sha256,
        ),
    )


def build_proposal(
    *,
    proposal_id: str,
    title: str,
    created_at: datetime,
    source: ProposalSource,
    observation: Observation,
    affected_artifacts: list[str],
    suggested_change: SuggestedChange,
    validation: ProposalValidation | None = None,
    confidence: Confidence = "medium",
    actor: str = "lab",
) -> Proposal:
    """Assemble a proposal seeded with the ``observed`` → ``proposed`` lifecycle."""
    lifecycle = [
        LifecycleEvent(state="observed", at=created_at, actor=actor),
        LifecycleEvent(state="proposed", at=created_at, actor=actor),
    ]
    return Proposal(
        proposal_id=proposal_id,
        title=title,
        status=ProposalStatus.PROPOSED,
        created_at=created_at,
        source=source,
        observation=observation,
        affected_artifacts=list(affected_artifacts),
        suggested_change=suggested_change,
        validation=validation or ProposalValidation(),
        confidence=confidence,
        lifecycle=lifecycle,
    )


def write_proposal(root: Path, proposal: Proposal) -> Path:
    path = proposal_path(root, proposal.proposal_id)
    manifests.atomic_write_text(path, manifests.proposal_to_yaml(proposal))
    return path


def load_proposal(path: Path) -> Proposal:
    return manifests.proposal_from_yaml(path.read_text(encoding="utf-8"))


def link_issue(
    proposal: Proposal, issue_url: str, *, at: datetime, actor: str
) -> Proposal:
    """Attach a source issue idempotently.

    Once ``links.source_issue`` is set, subsequent calls are a no-op — public
    history is never silently rewritten. Returns a copy; the input is untouched.
    """
    if proposal.links.source_issue:
        return proposal
    updated = proposal.model_copy(deep=True)
    updated.links.source_issue = issue_url
    updated.status = ProposalStatus.ISSUE_CREATED
    updated.lifecycle.append(LifecycleEvent(state="issue_created", at=at, actor=actor))
    return updated
