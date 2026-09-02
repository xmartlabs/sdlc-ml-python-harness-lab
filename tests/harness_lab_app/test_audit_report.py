"""The client-facing audit document says no more than the evidence does."""

from __future__ import annotations

from harness_lab_app.audit_report import build_report
from harness_lab_app.models import (
    Case,
    CaseType,
    Finding,
    FindingSeverity,
    FindingStatus,
    TargetRepository,
)


def _target() -> TargetRepository:
    return TargetRepository(
        target_id="acme",
        name="Acme API",
        repository_url="https://example.invalid/acme.git",
    )


def _readiness(**over):
    base = {
        "commit": "abc123def456",
        "ref": "main",
        "checks": [
            {
                "label": "Validation gate",
                "status": "missing",
                "detail": "Not configured.",
            },
            {
                "label": "Agent rules file",
                "status": "missing",
                "detail": "Not detected.",
            },
            {"label": "Skills", "status": "missing", "detail": "Not detected."},
        ],
        "suggested_validation_commands": ["npm test"],
        "suggested_setup_commands": ["npm ci"],
    }
    base.update(over)
    return base


def test_an_empty_corpus_reports_reading_not_measurement(session):
    report = build_report(session, _target(), _readiness())

    assert "# Repository audit — Acme API" in report
    assert "No runs have been recorded" in report
    assert "None accepted yet" in report  # findings
    # The inference reaches the client document, labelled as not yet adopted.
    assert "`npm test`" in report
    assert "not yet adopted" in report


def test_next_steps_follow_from_what_is_missing(session):
    report = build_report(session, _target(), _readiness())

    assert "Adopt an objective validation gate" in report
    assert "Add an agent rules file" in report
    assert "native-versus-injected experiment" in report


def test_only_signed_findings_enter_the_document(session):
    session.add(
        Finding(
            finding_id="no-lint",
            title="No lint gate",
            body="",
            severity=FindingSeverity.medium,
            status=FindingStatus.open,
            author="marcos",
            target_repo_id="acme",
            recommendation="Add one and measure.",
        )
    )
    session.add(
        Finding(
            finding_id="noise-rejected",
            title="A dismissed pattern",
            body="",
            severity=FindingSeverity.low,
            status=FindingStatus.dismissed,
            target_repo_id="acme",
        )
    )
    session.commit()

    report = build_report(session, _target(), _readiness())

    assert "No lint gate" in report
    assert "by marcos" in report
    assert "A dismissed pattern" not in report


def test_derived_cases_show_their_scoring_mode(session):
    session.add(
        Case(
            case_id="acme-fix-1",
            name="Fix the flaky retry",
            type=CaseType.repo_task,
            tags=["derived", "acme"],
            validation_commands=[],
        )
    )
    session.commit()

    report = build_report(session, _target(), _readiness())

    assert "acme-fix-1" in report
    assert "manual scoring" in report
