"""Findings and assessments: the consulting layer over the run corpus."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.findings import RUBRIC, TESTABLE_STATUSES, validate_assessment
from harness_lab_app.models import (
    Assessment,
    ConditionRole,
    Finding,
    FindingSeverity,
    FindingStatus,
)

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
            status=data.status,
            origin=data.origin,
            author=data.author,
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

    # --- suggestions ------------------------------------------------------

    def suggestions(self) -> list[dict[str, Any]]:
        """Patterns the corpus supports, minus the ones already ruled on.

        Computed on demand and never written. A suggestion only reaches the
        database as the *consequence* of a person accepting or rejecting it,
        which is what keeps "the lab noticed something" and "the lab claims
        something" distinct (§14).
        """
        from harness_lab_app import suggestions
        from harness_lab_app.leaderboard_service import LeaderboardService
        from harness_lab_app.overview_service import OverviewService

        ruled_on = {
            finding.origin
            for finding in self.session.exec(select(Finding)).all()
            if finding.origin
        }
        return suggestions.build(
            LeaderboardService(self.session).list_rows(),
            score_signal=OverviewService(self.session)._score_signal(),
            ruled_on=ruled_on,
        )

    def _suggestion(self, key: str) -> dict[str, Any]:
        for candidate in self.suggestions():
            if candidate["key"] == key:
                return candidate
        raise FindingNotFoundError(
            f"No open suggestion with key '{key}'. It may already have been "
            "accepted or rejected."
        )

    def accept_suggestion(
        self,
        key: str,
        *,
        author: str,
        finding_id: str = "",
        overrides: dict[str, Any] | None = None,
    ) -> tuple[Finding, list[str]]:
        """Turn a suggestion into a finding of record, with a name on it.

        ``author`` is required, and that is the whole point of the method
        existing. Everything else is editable before acceptance — the operator
        may rewrite the title, the body and the recommendation — because a
        finding a person signs must be a finding that person wrote.
        """
        from harness_lab_app.schemas import FindingCreate

        author = (author or "").strip()
        if not author:
            raise FindingError(
                "Accepting a suggestion requires an author. A finding nobody "
                "owns is an assertion nobody has to defend."
            )
        suggestion = {**self._suggestion(key), **(overrides or {})}
        finding, warnings = self.create_finding(
            FindingCreate(
                finding_id=finding_id.strip() or _finding_id_for(key),
                title=str(suggestion["title"]),
                body=str(suggestion.get("body", "")),
                severity=FindingSeverity(str(suggestion.get("severity", "medium"))),
                status=FindingStatus.open,
                origin=key,
                author=author,
                target_repo_id=str(suggestion.get("target_repo_id", "")),
                evidence_run_ids=list(suggestion.get("evidence_run_ids", [])),
                affected_case_ids=list(suggestion.get("affected_case_ids", [])),
                recommendation=str(suggestion.get("recommendation", "")),
                intervention=str(suggestion.get("intervention", "")),
            )
        )
        finding.accepted_at = datetime.now(UTC)
        self.session.add(finding)
        self.session.commit()
        self.session.refresh(finding)
        return finding, warnings

    def reject_suggestion(self, key: str, *, author: str, reason: str = "") -> Finding:
        """Record that a person looked at a suggestion and declined it.

        Written down rather than discarded: the rejection is what stops the
        pattern being re-suggested every week, and a reviewer six months later
        deserves to see that somebody already considered it.
        """
        suggestion = self._suggestion(key)
        finding, _ = self.create_finding(
            _rejection_payload(key, suggestion, author=author, reason=reason)
        )
        return finding

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


def _finding_id_for(key: str) -> str:
    """A slug from a suggestion key: ``regression:cli-v1`` → ``regression-cli-v1``."""
    slug = re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-")
    return slug[:64] or "suggested-finding"


def _rejection_payload(
    key: str, suggestion: dict[str, Any], *, author: str, reason: str
) -> FindingCreate:
    from harness_lab_app.schemas import FindingCreate

    return FindingCreate(
        finding_id=f"{_finding_id_for(key)}-rejected"[:64],
        title=str(suggestion["title"]),
        body=reason or "Reviewed and not taken forward.",
        severity=FindingSeverity(str(suggestion.get("severity", "medium"))),
        status=FindingStatus.dismissed,
        origin=key,
        author=author,
        target_repo_id=str(suggestion.get("target_repo_id", "")),
        evidence_run_ids=list(suggestion.get("evidence_run_ids", [])),
        affected_case_ids=list(suggestion.get("affected_case_ids", [])),
    )
