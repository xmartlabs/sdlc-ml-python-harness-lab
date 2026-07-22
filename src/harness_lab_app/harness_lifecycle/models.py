"""Pydantic contracts and lifecycle enums for versioned harness proposals.

The YAML file on disk is the reviewed source of truth (plan §6.1); these models
are its schema. Database rows, when added later, are only UI indexes over this.
"""

from __future__ import annotations

import re
from datetime import datetime  # noqa: TC003 (Pydantic needs it at runtime)
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PROPOSAL_ID_RE = re.compile(r"^HEP-\d{4}-\d{3}$")

Confidence = Literal["low", "medium", "high"]
CriterionOperator = Literal["lte", "gte", "eq", "lt", "gt"]


class ProposalStatus(StrEnum):
    """Lifecycle states a proposal moves through (plan §8).

    Phase 3 uses the first three; later phases add the release/validation states.
    """

    OBSERVED = "observed"
    PROPOSED = "proposed"
    ISSUE_CREATED = "issue_created"
    IN_IMPLEMENTATION = "in_implementation"
    RELEASED = "released"


class LifecycleEvent(BaseModel):
    state: str
    at: datetime
    actor: str


class SuccessCriterion(BaseModel):
    metric: str
    operator: CriterionOperator
    value: bool | int | float | str


class RunArtifacts(BaseModel):
    audit: str
    metadata_sha256: str


class RunEvidence(BaseModel):
    run_id: str
    condition_hash: str
    artifacts: RunArtifacts


class ProposalSource(BaseModel):
    repository: str
    runs: list[RunEvidence] = Field(default_factory=list)


class Observation(BaseModel):
    failure_mode: str
    expected_behavior: str
    evidence_summary: str


class SuggestedChange(BaseModel):
    summary: str


class ProposalValidation(BaseModel):
    suggested_cases: list[str] = Field(default_factory=list)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)


class ProposalLinks(BaseModel):
    source_issue: str | None = None
    source_pull_requests: list[str] = Field(default_factory=list)
    releases: list[str] = Field(default_factory=list)
    validations: list[str] = Field(default_factory=list)
    guide_pull_request: str | None = None


class Proposal(BaseModel):
    schema_version: int = 1
    proposal_id: str
    title: str
    status: ProposalStatus = ProposalStatus.PROPOSED
    created_at: datetime
    source: ProposalSource
    observation: Observation
    affected_artifacts: list[str] = Field(default_factory=list)
    suggested_change: SuggestedChange
    validation: ProposalValidation = Field(default_factory=ProposalValidation)
    confidence: Confidence = "medium"
    links: ProposalLinks = Field(default_factory=ProposalLinks)
    lifecycle: list[LifecycleEvent] = Field(default_factory=list)

    @field_validator("proposal_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not PROPOSAL_ID_RE.match(value):
            raise ValueError(f"Invalid proposal_id '{value}'. Expected HEP-YYYY-NNN.")
        return value


# --------------------------------------------------------------------------- #
# Installed version manifest (.harness-version.yaml — plan §6.3)
# --------------------------------------------------------------------------- #


class InstalledRef(BaseModel):
    repository: str
    ref: str
    commit: str
    channel: str = "governance"
    adopted_at: datetime


class SyncInfo(BaseModel):
    protocol: int = 1
    tools: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    release_manifest_sha256: str | None = None


class ValidationRef(BaseModel):
    validation_id: str
    status: str
    suite: str
    report: str


class InstalledManifest(BaseModel):
    """The lab's canonical record of the adopted template release."""

    schema_version: int = 1
    installed: InstalledRef
    sync: SyncInfo = Field(default_factory=SyncInfo)
    validation: ValidationRef | None = None
