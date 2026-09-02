"""Suggested findings — what the lab may notice, and where its authority stops.

The boundary these tests defend is §14's: the lab computes patterns, a person
signs findings. Nothing here may create a finding of record on its own, an
accepted suggestion must carry the name of whoever accepted it, and a rejected
one must stay rejected instead of returning next week.
"""

from __future__ import annotations

import pytest

from harness_lab_app import suggestions
from harness_lab_app.finding_service import (
    FindingError,
    FindingNotFoundError,
    FindingService,
)
from harness_lab_app.models import FindingStatus


def _row(**over):
    base = {
        "run_id": "r1",
        "case_id": "c1",
        "resolved": True,
        "chain_depth": 0,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(over)
    return base


def _history(*verdicts, case_id="c1"):
    """One row per verdict, oldest first."""
    return [
        _row(
            run_id=f"{case_id}-{index}",
            case_id=case_id,
            resolved=verdict,
            created_at=f"2026-01-{index + 1:02d}T00:00:00+00:00",
        )
        for index, verdict in enumerate(verdicts)
    ]


# --------------------------------------------------------------------------
# What the corpus supports
# --------------------------------------------------------------------------


def test_a_case_that_passed_and_now_fails_is_suggested_as_a_regression():
    found = suggestions.from_regressions(_history(True, True, False))
    assert [item["key"] for item in found] == ["regression:c1"]
    assert found[0]["severity"] == "high"
    # Both ends of the evidence: the last run that worked and the one that did not.
    assert found[0]["evidence_run_ids"] == ["c1-1", "c1-2"]


def test_a_case_that_never_passed_is_an_open_problem_not_a_regression():
    rows = _history(False, False, False)
    assert suggestions.from_regressions(rows) == []
    found = suggestions.from_repeated_failures(rows)
    assert [item["key"] for item in found] == ["repeated-failure:c1"]


def test_a_single_failure_is_not_yet_a_pattern():
    assert suggestions.from_repeated_failures(_history(False)) == []


def test_a_continued_run_is_not_read_as_the_harness_behaving():
    """A human steered a continuation, so it says nothing about the harness."""
    rows = _history(True, True)
    rows.append(_row(run_id="c1-cont", resolved=False, chain_depth=1))
    assert suggestions.from_regressions(rows) == []


def test_an_unvalidated_run_cannot_break_a_streak():
    rows = _history(True, True)
    rows.append(_row(run_id="c1-unscored", resolved=None))
    assert suggestions.from_regressions(rows) == []


def test_low_adherence_is_offered_and_labelled_as_a_judgement():
    found = suggestions.from_score_signal(
        {"adherence": {"mean": 0.3, "runs": 8}, "redundant": {}}
    )
    assert [item["key"] for item in found] == ["low-adherence"]
    assert "judgement" in found[0]["body"]


def test_low_adherence_over_two_runs_is_not_offered_at_all():
    found = suggestions.from_score_signal({"adherence": {"mean": 0.1, "runs": 2}})
    assert found == []


def test_every_suggestion_says_it_is_only_a_suggestion():
    found = suggestions.build(
        _history(True, False),
        score_signal={"adherence": {"mean": 0.2, "runs": 5}},
    )
    assert found, "expected the corpus to support at least one suggestion"
    assert all(item["status"] == "suggested" for item in found)
    assert all(item["recommendation"] for item in found), (
        "a suggestion that cannot become an experiment is a dead end"
    )


def test_the_worst_suggestion_is_offered_first():
    found = suggestions.build(
        [*_history(True, False, case_id="regressed")],
        score_signal={"redundant": {"mean": 2.0, "runs": 5}},
    )
    assert [item["severity"] for item in found] == ["high", "low"]


def test_a_suggestion_already_ruled_on_is_not_offered_again():
    rows = _history(True, False)
    assert suggestions.build(rows, ruled_on={"regression:c1"}) == []


# --------------------------------------------------------------------------
# Accepting and rejecting — where a person becomes accountable
# --------------------------------------------------------------------------


@pytest.fixture
def offered(monkeypatch):
    """One suggestion on offer, without needing a corpus behind it."""
    item = suggestions._suggestion(
        "regression:cli-textstats",
        "'cli-textstats' regressed",
        "It passed twice and now fails.",
        severity="high",
        recommendation="Compare the two harness states against this case.",
        evidence_run_ids=["r1", "r2"],
        affected_case_ids=["cli-textstats"],
        target_repo_id="payments",
        source="regression",
    )
    monkeypatch.setattr(
        suggestions,
        "build",
        lambda *_args, **kwargs: (
            [] if item["key"] in (kwargs.get("ruled_on") or set()) else [item]
        ),
    )
    return item


def test_a_suggestion_is_not_a_finding_until_somebody_accepts_it(session, offered):
    service = FindingService(session)
    assert [item["key"] for item in service.suggestions()] == [offered["key"]]
    assert service.list_findings() == []


def test_accepting_a_suggestion_requires_a_name(session, offered):
    with pytest.raises(FindingError, match="requires an author"):
        FindingService(session).accept_suggestion(offered["key"], author="  ")


def test_an_accepted_suggestion_becomes_a_finding_of_record(session, offered):
    finding, _warnings = FindingService(session).accept_suggestion(
        offered["key"], author="marcos"
    )
    assert finding.status == FindingStatus.open
    assert finding.author == "marcos"
    assert finding.origin == offered["key"]
    assert finding.accepted_at is not None
    assert finding.evidence_run_ids == ["r1", "r2"]
    assert finding.target_repo_id == "payments"


def test_an_accepted_suggestion_is_no_longer_offered(session, offered):
    service = FindingService(session)
    service.accept_suggestion(offered["key"], author="marcos")
    assert service.suggestions() == []


def test_the_accepting_person_may_rewrite_what_they_sign(session, offered):
    finding, _ = FindingService(session).accept_suggestion(
        offered["key"],
        author="marcos",
        overrides={"title": "Retry loop broke cli-textstats"},
    )
    assert finding.title == "Retry loop broke cli-textstats"
    # The key is not editable: it is what stops the pattern re-appearing.
    assert finding.origin == offered["key"]


def test_a_rejected_suggestion_is_recorded_rather_than_discarded(session, offered):
    service = FindingService(session)
    finding = service.reject_suggestion(
        offered["key"], author="marcos", reason="Flaky case, not the harness."
    )
    assert finding.status == FindingStatus.dismissed
    assert finding.author == "marcos"
    assert "Flaky case" in finding.body
    assert service.suggestions() == []


def test_a_suggestion_can_only_be_ruled_on_once(session, offered):
    service = FindingService(session)
    service.accept_suggestion(offered["key"], author="marcos")
    with pytest.raises(FindingNotFoundError, match="already have been"):
        service.reject_suggestion(offered["key"], author="marcos")


# --------------------------------------------------------------------------
# Over the API
# --------------------------------------------------------------------------


def _endpoint(path: str, method: str):
    from harness_lab_app.api import create_app

    for route in create_app().router.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


def test_suggestions_are_listed_over_the_api(session, offered):
    payload = _endpoint("/api/findings/suggestions", "GET")(session=session)
    assert [item["key"] for item in payload["suggestions"]] == [offered["key"]]


def test_accepting_over_the_api_without_an_author_is_a_bad_request(session, offered):
    from fastapi import HTTPException

    from harness_lab_app.schemas import SuggestionAccept

    endpoint = _endpoint("/api/findings/suggestions/{key:path}/accept", "POST")
    with pytest.raises(HTTPException) as error:
        endpoint(key=offered["key"], data=SuggestionAccept(author=""), session=session)
    assert error.value.status_code == 422


def test_accepting_an_unknown_suggestion_is_a_not_found(session, offered):
    from fastapi import HTTPException

    from harness_lab_app.schemas import SuggestionAccept

    endpoint = _endpoint("/api/findings/suggestions/{key:path}/accept", "POST")
    with pytest.raises(HTTPException) as error:
        endpoint(
            key="regression:nothing",
            data=SuggestionAccept(author="marcos"),
            session=session,
        )
    assert error.value.status_code == 404


def test_rejecting_over_the_api_records_who_declined_it(session, offered):
    from harness_lab_app.schemas import SuggestionReject

    endpoint = _endpoint("/api/findings/suggestions/{key:path}/reject", "POST")
    result = endpoint(
        key=offered["key"],
        data=SuggestionReject(author="marcos", reason="Known flake."),
        session=session,
    )
    finding = result["finding"] if isinstance(result, dict) else result
    assert finding.author == "marcos"
