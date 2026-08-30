"""Findings and assessments: the consulting layer over the run corpus."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.findings import RUBRIC, TESTABLE_STATUSES, validate_assessment
from harness_lab_app.models import Assessment, ConditionRole, Finding

if TYPE_CHECKING:
    from harness_lab_app.schemas import (
        AssessmentCreate,
        FindingCreate,
        FindingUpdate,
    )

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class FindingError(ValueError):
    """A finding or assessment could not be recorded as asked."""


class FindingNotFoundError(FindingError):
    pass


class FindingService:
    def __init__(self, session: Session):
        self.session = session

    # --- findings ---------------------------------------------------------

    def list_findings(self) -> list[Finding]:
        rows = list(self.session.exec(select(Finding)).all())
        # Severity first, then newest: a reader should meet the worst thing
        # before the most recent thing.
        rank = {"high": 0, "medium": 1, "low": 2}
        return sorted(
            rows, key=lambda row: (rank.get(row.severity, 9), -row.id if row.id else 0)
        )

    def get_finding(self, finding_id: str) -> Finding:
        row = self.session.exec(
            select(Finding).where(Finding.finding_id == finding_id)
        ).first()
        if row is None:
            raise FindingNotFoundError(f"Unknown finding: {finding_id}")
        return row

    def create_finding(self, data: FindingCreate) -> tuple[Finding, list[str]]:
        finding_id = data.finding_id.strip()
        if not _SLUG.match(finding_id):
            raise FindingError(
                "finding_id must be a lowercase slug (a-z, 0-9, dashes), 2-64 chars."
            )
        if self.session.exec(
            select(Finding).where(Finding.finding_id == finding_id)
        ).first():
            raise FindingError(f"Finding already exists: {finding_id}")

        warnings: list[str] = []
        if not data.evidence_run_ids:
            # Not an error: an observation made while reading a repository is a
            # legitimate starting point. But it is an opinion until runs back
            # it, and it must not be presented as anything else.
            warnings.append(
                "This finding cites no runs. Until it does it is an observation, "
                "not evidence — label it as such wherever it is shown."
            )
        if not data.recommendation.strip():
            warnings.append(
                "No recommendation. A finding without one cannot become an "
                "experiment, so nothing will ever settle it."
            )

        finding = Finding(
            finding_id=finding_id,
            title=data.title,
            body=data.body,
            severity=data.severity,
            target_repo_id=data.target_repo_id,
            evidence_run_ids=list(data.evidence_run_ids),
            affected_case_ids=list(data.affected_case_ids),
            recommendation=data.recommendation,
            intervention=data.intervention,
        )
        self.session.add(finding)
        self.session.commit()
        self.session.refresh(finding)
        return finding, warnings

    def update_finding(self, finding_id: str, data: FindingUpdate) -> Finding:
        finding = self.get_finding(finding_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(finding, field, value)
        finding.updated_at = datetime.now(UTC)
        self.session.add(finding)
        self.session.commit()
        self.session.refresh(finding)
        return finding

    def experiment_draft(self, finding_id: str) -> dict[str, Any]:
        """The experiment that would test this finding's recommendation.

        Returned as a draft rather than created: the design needs cases and a
        repository the operator chooses, and an experiment auto-created from a
        sentence would be exactly the unanswerable design ADR-0033 refuses.
        """
        finding = self.get_finding(finding_id)
        if finding.status not in TESTABLE_STATUSES:
            raise FindingError(
                f"Finding '{finding_id}' is {finding.status.value}: its question "
                "has already been answered. Re-testing it is a new finding."
            )
        if not finding.recommendation.strip():
            raise FindingError(
                f"Finding '{finding_id}' has no recommendation, so there is "
                "nothing to test."
            )
        return {
            "experiment_id": f"{finding.finding_id}-test",
            "name": f"Test: {finding.title}",
            "question": (
                f"Does {finding.intervention or finding.recommendation} improve "
                f"outcomes on {finding.target_repo_id or 'the affected repository'}?"
            ),
            "mode": "harness_evaluation",
            "case_ids": list(finding.affected_case_ids),
            "repetitions": 5,
            "conditions": [
                {
                    "label": "baseline",
                    "role": ConditionRole.control,
                    "description": "The setup as it is today.",
                    "overrides": (
                        {"target_repo_id": finding.target_repo_id}
                        if finding.target_repo_id
                        else {}
                    ),
                },
                {
                    "label": "intervention",
                    "role": ConditionRole.treatment,
                    "description": finding.intervention or finding.recommendation,
                    "overrides": (
                        {"target_repo_id": finding.target_repo_id}
                        if finding.target_repo_id
                        else {}
                    ),
                },
            ],
            "notes": (
                f"Drafted from finding {finding.finding_id}. Fill in the harness "
                "axis the intervention changes — a design that varies nothing "
                "about the harness will be refused."
            ),
        }

    # --- assessments ------------------------------------------------------

    def list_assessments(self) -> list[Assessment]:
        return list(self.session.exec(select(Assessment)).all())

    def get_assessment(self, assessment_id: str) -> Assessment:
        row = self.session.exec(
            select(Assessment).where(Assessment.assessment_id == assessment_id)
        ).first()
        if row is None:
            raise FindingNotFoundError(f"Unknown assessment: {assessment_id}")
        return row

    def create_assessment(self, data: AssessmentCreate) -> tuple[Assessment, list[str]]:
        assessment_id = data.assessment_id.strip()
        if not _SLUG.match(assessment_id):
            raise FindingError(
                "assessment_id must be a lowercase slug (a-z, 0-9, dashes)."
            )
        if self.session.exec(
            select(Assessment).where(Assessment.assessment_id == assessment_id)
        ).first():
            raise FindingError(f"Assessment already exists: {assessment_id}")
        try:
            warnings = validate_assessment(data.dimensions)
        except ValueError as exc:
            raise FindingError(str(exc)) from exc

        assessment = Assessment(
            assessment_id=assessment_id,
            organization=data.organization,
            summary=data.summary,
            dimensions=data.dimensions,
            assessed_by=data.assessed_by,
        )
        self.session.add(assessment)
        self.session.commit()
        self.session.refresh(assessment)
        return assessment, warnings

    @staticmethod
    def rubric() -> list[dict[str, Any]]:
        return [dimension.__dict__ for dimension in RUBRIC]
