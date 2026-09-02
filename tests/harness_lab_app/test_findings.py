"""Findings and maturity assessments — the consulting layer (ADR-0035).

The behaviours worth protecting are the ones that stop a claim outrunning its
evidence: an unbacked finding is labelled, a maturity level above 2 needs
evidence, and an already-answered finding cannot be quietly re-tested.
"""

from __future__ import annotations

import pytest

from harness_lab_app.finding_service import FindingError, FindingService
from harness_lab_app.findings import RUBRIC, RUBRIC_BY_KEY, validate_assessment
from harness_lab_app.models import ConditionRole, FindingSeverity, FindingStatus
from harness_lab_app.schemas import AssessmentCreate, FindingCreate, FindingUpdate


def _finding(**over) -> FindingCreate:
    base = {
        "finding_id": "migrations-edited",
        "title": "Agents edit legacy migration files",
        "severity": FindingSeverity.high,
        "target_repo_id": "payments",
        "evidence_run_ids": ["r1", "r2"],
        "affected_case_ids": ["add-endpoint"],
        "recommendation": "Add a migration-safety skill",
        "intervention": "migration-safety skill",
    }
    base.update(over)
    return FindingCreate(**base)


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


def test_a_finding_with_no_runs_is_labelled_an_opinion(session):
    _finding_row, warnings = FindingService(session).create_finding(
        _finding(evidence_run_ids=[])
    )
    assert any("not evidence" in warning for warning in warnings)


def test_a_finding_with_no_recommendation_can_never_be_settled(session):
    _row, warnings = FindingService(session).create_finding(_finding(recommendation=""))
    assert any("cannot become an experiment" in warning for warning in warnings)


def test_a_well_evidenced_finding_warns_about_nothing(session):
    _row, warnings = FindingService(session).create_finding(_finding())
    assert warnings == []


def test_findings_are_listed_worst_first(session):
    service = FindingService(session)
    service.create_finding(_finding(finding_id="low-one", severity=FindingSeverity.low))
    service.create_finding(
        _finding(finding_id="high-one", severity=FindingSeverity.high)
    )
    service.create_finding(
        _finding(finding_id="medium-one", severity=FindingSeverity.medium)
    )
    assert next(row.finding_id for row in service.list_findings()) == "high-one"


def test_a_finding_becomes_a_control_treatment_draft(session):
    service = FindingService(session)
    service.create_finding(_finding())
    draft = service.experiment_draft("migrations-edited")

    assert draft["mode"] == "harness_evaluation"
    assert draft["case_ids"] == ["add-endpoint"]
    roles = [condition["role"] for condition in draft["conditions"]]
    assert roles == [ConditionRole.control, ConditionRole.treatment]
    # Both arms sit on the repository the finding was observed in.
    assert all(
        condition["overrides"]["target_repo_id"] == "payments"
        for condition in draft["conditions"]
    )
    # The draft is honest about what it cannot fill in.
    assert "harness axis" in draft["notes"]


def test_an_already_answered_finding_is_not_re_tested_silently(session):
    service = FindingService(session)
    service.create_finding(_finding())
    service.update_finding(
        "migrations-edited", FindingUpdate(status=FindingStatus.validated)
    )
    with pytest.raises(FindingError, match="already been answered"):
        service.experiment_draft("migrations-edited")


def test_a_finding_with_nothing_to_test_is_refused_a_draft(session):
    service = FindingService(session)
    service.create_finding(_finding(recommendation="", intervention=""))
    with pytest.raises(FindingError, match="nothing to test"):
        service.experiment_draft("migrations-edited")


def test_a_refuted_finding_keeps_its_record(session):
    """The part a consultancy is tempted to drop."""
    service = FindingService(session)
    service.create_finding(_finding())
    updated = service.update_finding(
        "migrations-edited",
        FindingUpdate(
            status=FindingStatus.refuted,
            experiment_id="migrations-edited-test",
            outcome="No difference beyond run-to-run variance.",
        ),
    )
    assert updated.status == FindingStatus.refuted
    assert updated.experiment_id == "migrations-edited-test"
    assert updated.outcome


# --------------------------------------------------------------------------
# Assessment rubric
# --------------------------------------------------------------------------


def test_every_rubric_dimension_describes_five_observable_levels():
    assert len(RUBRIC) == 7
    for dimension in RUBRIC:
        assert len(dimension.levels) == 5
        assert dimension.question.endswith("?")
        assert all(level.strip() for level in dimension.levels)


def test_a_high_score_without_evidence_is_refused():
    with pytest.raises(ValueError, match="no evidence"):
        validate_assessment(
            {"evaluation": {"level": 4, "rationale": "they said so", "evidence": []}}
        )


def test_a_low_score_needs_no_evidence():
    """Claiming a practice is absent is not a claim that needs backing."""
    warnings = validate_assessment(
        {"evaluation": {"level": 1, "rationale": "nothing observed", "evidence": []}}
    )
    assert not any("no evidence" in warning for warning in warnings)


def test_an_unscored_dimension_is_reported_as_unknown_not_zero():
    warnings = validate_assessment(
        {"evaluation": {"level": 1, "rationale": "x", "evidence": []}}
    )
    assert any("never as zero" in warning for warning in warnings)


@pytest.mark.parametrize("level", [0, 6, "high", None])
def test_a_level_outside_the_rubric_is_refused(level):
    with pytest.raises(ValueError, match="between 1 and 5"):
        validate_assessment({"evaluation": {"level": level, "evidence": ["r1"]}})


def test_an_invented_dimension_is_refused():
    with pytest.raises(ValueError, match="Unknown rubric dimension"):
        validate_assessment({"vibes": {"level": 3, "evidence": ["r1"]}})


def test_creating_an_assessment_surfaces_its_gaps(session):
    _row, warnings = FindingService(session).create_assessment(
        AssessmentCreate(
            assessment_id="acme-2026-q3",
            organization="Acme",
            dimensions={
                key: {"level": 2, "rationale": "observed", "evidence": []}
                for key in RUBRIC_BY_KEY
            },
        )
    )
    assert warnings == []


def test_an_assessment_that_claims_maturity_without_evidence_is_refused(session):
    with pytest.raises(FindingError, match="no evidence"):
        FindingService(session).create_assessment(
            AssessmentCreate(
                assessment_id="acme-2026-q3",
                organization="Acme",
                dimensions={"governance": {"level": 5, "rationale": "x"}},
            )
        )
