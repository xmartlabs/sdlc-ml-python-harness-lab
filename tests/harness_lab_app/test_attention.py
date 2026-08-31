"""The home page's action list — every item must be worth acting on (§4, §17).

These tests are mostly about what the list *refuses* to say. A home page that
lists a run id, or a number with no way into it, or a warning that is really
just an aggregate, sends the reader back to the architecture diagram — which is
the problem this section exists to fix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from harness_lab_app.attention_service import AttentionService, evidence_summary
from harness_lab_app.models import (
    Experiment,
    ExperimentMode,
    ExperimentStatus,
    Finding,
    FindingSeverity,
    FindingStatus,
    Run,
    RunState,
    Score,
    TargetRepository,
)


def _feed(session) -> dict:
    return AttentionService(session).build()


def _kinds(session) -> list[str]:
    return [item["kind"] for item in _feed(session)["items"]]


def _by_kind(session, kind: str) -> dict:
    return next(item for item in _feed(session)["items"] if item["kind"] == kind)


def _run(session, **over) -> Run:
    base = {
        "run_id": f"run-{datetime.now(UTC).timestamp()}",
        "run_config_id": 1,
        "state": RunState.completed,
    }
    base.update(over)
    row = Run(**base)
    session.add(row)
    session.commit()
    return row


# --------------------------------------------------------------------------
# An empty lab has nothing to shout about
# --------------------------------------------------------------------------


def test_a_lab_with_nothing_in_it_says_nothing_needs_attention(session):
    feed = _feed(session)
    assert feed["items"] == []
    assert feed["clear"] is True


# --------------------------------------------------------------------------
# The signals
# --------------------------------------------------------------------------


def test_a_finished_run_with_no_scores_is_a_backlog_not_a_failure(session):
    _run(session, run_id="r1")
    item = _by_kind(session, "unscored_runs")
    assert item["severity"] == "info"
    assert item["count"] == 1
    assert item["action"] == "Score the backlog"


def test_a_scored_run_leaves_the_backlog(session):
    _run(session, run_id="r1")
    session.add(
        Score(run_id="r1", name="task_validation_pass", version=1, value_bool=True)
    )
    session.commit()
    assert "unscored_runs" not in _kinds(session)


def test_a_failed_experiment_run_is_critical_and_explains_why(session):
    _run(session, run_id="r1", state=RunState.failed, experiment_id="exp-1")
    item = _by_kind(session, "failed_experiment_runs")
    assert item["severity"] == "critical"
    assert "unbalanced" in item["detail"]


def test_an_old_failure_is_not_still_shouting(session):
    _run(
        session,
        run_id="r1",
        state=RunState.failed,
        experiment_id="exp-1",
        created_at=datetime.now(UTC) - timedelta(days=90),
    )
    assert "failed_experiment_runs" not in _kinds(session)


def test_an_experiment_with_too_few_repetitions_is_flagged_before_it_is_read(session):
    session.add(
        Experiment(
            experiment_id="exp-1",
            name="exp-1",
            question="Does the skill help?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["c1"],
            repetitions=2,
            status=ExperimentStatus.running,
        )
    )
    session.commit()
    item = _by_kind(session, "thin_experiments")
    assert item["action"] == "Add repetitions"
    assert "stochastic" in item["detail"]


def test_a_well_powered_experiment_is_not_flagged(session):
    session.add(
        Experiment(
            experiment_id="exp-1",
            name="exp-1",
            question="Does the skill help?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["c1"],
            repetitions=10,
            status=ExperimentStatus.running,
        )
    )
    session.commit()
    assert "thin_experiments" not in _kinds(session)


def test_the_weakest_case_governs_whether_an_experiment_is_thin(session):
    """Ten repetitions overall does not rescue a case that runs once."""
    session.add(
        Experiment(
            experiment_id="exp-1",
            name="exp-1",
            question="Does the skill help?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["c1", "swe-1"],
            repetitions=10,
            case_repetitions={"swe-1": 1},
            status=ExperimentStatus.running,
        )
    )
    session.commit()
    assert "thin_experiments" in _kinds(session)


def test_a_repository_with_no_tests_cannot_enter_a_success_rate_and_says_so(session):
    session.add(
        TargetRepository(
            target_id="payments",
            name="payments",
            repository_url="https://github.com/acme/payments.git",
        )
    )
    session.commit()
    item = _by_kind(session, "repositories_without_gates")
    assert item["action"] == "Configure validation"
    assert "scored by hand" in item["detail"]


def test_a_repository_with_a_gate_is_not_flagged(session):
    session.add(
        TargetRepository(
            target_id="payments",
            name="payments",
            repository_url="https://github.com/acme/payments.git",
            validation_commands=["pytest -q"],
        )
    )
    session.commit()
    assert "repositories_without_gates" not in _kinds(session)


def test_an_open_finding_nobody_is_testing_offers_the_way_into_an_experiment(session):
    session.add(
        Finding(
            finding_id="migrations",
            title="Agents edit migrations",
            severity=FindingSeverity.high,
            status=FindingStatus.open,
            recommendation="Add a migration-safety skill",
        )
    )
    session.commit()
    item = _by_kind(session, "untested_findings")
    assert item["action"] == "Turn a finding into an experiment"


def test_a_finding_already_under_experiment_is_not_asking_for_anything(session):
    session.add(
        Finding(
            finding_id="migrations",
            title="Agents edit migrations",
            severity=FindingSeverity.high,
            status=FindingStatus.open,
            recommendation="Add a migration-safety skill",
            experiment_id="exp-1",
        )
    )
    session.commit()
    assert "untested_findings" not in _kinds(session)


def test_a_finding_with_no_recommendation_cannot_become_an_experiment_so_is_not_offered(
    session,
):
    session.add(
        Finding(
            finding_id="vague",
            title="Something feels off",
            severity=FindingSeverity.low,
            status=FindingStatus.open,
        )
    )
    session.commit()
    assert "untested_findings" not in _kinds(session)


def test_a_suggested_finding_is_offered_for_review_and_labelled_as_not_yet_a_finding(
    session, monkeypatch
):
    from harness_lab_app import suggestions

    monkeypatch.setattr(
        suggestions,
        "build",
        lambda *_args, **_kwargs: [suggestions._suggestion("k", "T", "B")],
    )
    item = _by_kind(session, "suggested_findings")
    assert "until someone accepts it" in item["detail"]
    assert item["route"].endswith("tab=suggested")


# --------------------------------------------------------------------------
# The shape of every item, whatever produced it
# --------------------------------------------------------------------------


@pytest.fixture
def busy(session):
    _run(session, run_id="r1")
    _run(session, run_id="r2", state=RunState.failed, experiment_id="exp-1")
    session.add(
        TargetRepository(
            target_id="payments",
            name="payments",
            repository_url="https://github.com/acme/payments.git",
        )
    )
    session.add(
        Finding(
            finding_id="migrations",
            title="Agents edit migrations",
            severity=FindingSeverity.high,
            status=FindingStatus.open,
            recommendation="Add a migration-safety skill",
        )
    )
    session.commit()
    return session


def test_every_item_carries_a_way_to_act_on_it(busy):
    items = _feed(busy)["items"]
    assert len(items) >= 3
    for item in items:
        assert item["action"], f"{item['kind']} has no call to action"
        assert item["route"].startswith("/"), f"{item['kind']} has nowhere to go"
        assert item["headline"] and item["detail"]


def test_no_item_shows_a_raw_identifier_in_its_headline(busy):
    """Ids belong on a detail page, never on the home page's action list."""
    for item in _feed(busy)["items"]:
        assert "run_id" not in item["headline"]
        assert "r1" not in item["headline"]
        assert "exp-1" not in item["headline"]


def test_the_worst_thing_is_listed_first(busy):
    severities = [item["severity"] for item in _feed(busy)["items"]]
    assert severities == sorted(
        severities, key=lambda value: {"critical": 0, "warning": 1, "info": 2}[value]
    )
    assert severities[0] == "critical"


# --------------------------------------------------------------------------
# The evidence summary (§17)
# --------------------------------------------------------------------------


def test_the_evidence_summary_stays_small(session):
    summary = evidence_summary(session)
    assert set(summary) == {
        "experiments_active",
        "repositories",
        "cases",
        "success",
        "cost",
        "findings_open",
        "harness_version",
        "window_days",
    }


def test_the_summary_reports_a_rate_with_its_denominator_not_a_bare_number(session):
    summary = evidence_summary(session)
    # No runs yet: no rate at all, rather than a misleading 0%.
    assert summary["success"]["rate"] is None
    assert summary["success"]["n"] == 0


def test_the_summary_counts_only_what_is_live(session):
    session.add(
        TargetRepository(
            target_id="payments",
            name="payments",
            repository_url="https://github.com/acme/payments.git",
            validation_commands=["pytest -q"],
        )
    )
    session.add(
        Experiment(
            experiment_id="exp-1",
            name="exp-1",
            question="q",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["c1"],
            repetitions=5,
            status=ExperimentStatus.running,
        )
    )
    session.add(
        Experiment(
            experiment_id="exp-2",
            name="exp-2",
            question="q",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["c1"],
            repetitions=5,
            status=ExperimentStatus.draft,
        )
    )
    session.commit()
    summary = evidence_summary(session)
    assert summary["experiments_active"] == 1
    assert summary["repositories"] == 1


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


def test_the_home_page_can_ask_what_needs_attention(session):
    _run(session, run_id="r1")
    payload = _endpoint("/api/attention", "GET")(session=session)
    assert payload["clear"] is False
    assert payload["items"][0]["action"]


def test_the_home_page_can_ask_for_the_evidence_summary(session):
    payload = _endpoint("/api/evidence-summary", "GET")(session=session)
    assert payload["repositories"] == 0


def test_every_attention_route_is_one_the_app_actually_serves(session):
    """A call to action that lands nowhere teaches people to stop clicking them.

    The frontend owns these paths, so this is a contract test on the strings:
    each one must be a route the app declares, not a path that reads plausibly.
    """
    # The route table the React app declares (frontend/src/App.tsx). Kept here
    # because the attention feed is the only backend that emits UI paths.
    known = {
        "/",
        "/evaluate/new",
        "/evaluate/experiments",
        "/evaluate/audit",
        "/evidence/findings",
        "/evidence/cases",
        "/evidence/repositories",
        "/evidence/skills",
        "/advanced/runs",
        "/advanced/compare",
        "/advanced/benchmarks",
        "/advanced/improvements",
        "/system/health",
        "/system/guide",
        "/system/settings",
    }
    # One of each signal the feed can raise.
    _run(session, run_id="unscored-1")
    _run(session, run_id="failed-1", state=RunState.failed, experiment_id="exp-1")
    session.add(
        Experiment(
            experiment_id="exp-1",
            name="exp-1",
            question="Does it help?",
            mode=ExperimentMode.harness_evaluation,
            status=ExperimentStatus.complete,
            repetitions=2,
        )
    )
    session.add(
        Finding(
            finding_id="f-1",
            title="No lint gate",
            body="",
            severity=FindingSeverity.medium,
            status=FindingStatus.open,
            recommendation="Add one and measure.",
        )
    )
    session.add(
        TargetRepository(
            target_id="acme",
            name="acme",
            repository_url="https://example.invalid/acme.git",
        )
    )
    session.commit()

    routes = [item["route"] for item in _feed(session)["items"]]

    assert routes, "the seeded corpus should raise at least one signal"
    for route in routes:
        path = route.split("?")[0]
        assert path in known, f"{route} is not a route the app serves"


def test_evidence_summary_field_names_are_the_ones_the_home_page_reads(session):
    """A renamed field here blanks the home page, and nothing else catches it.

    The frontend's `EvidenceSummary` type is hand-written, so TypeScript checks
    the page against the *type*, not against this payload. When the two drifted
    — `cost.median` against `cost.median_usd` — the page called `.toFixed` on
    `undefined`, React unmounted the whole tree, and the app rendered blank with
    a green build and a green typecheck. Pin the names.
    """
    summary = evidence_summary(session)

    assert set(summary) == {
        "experiments_active",
        "repositories",
        "cases",
        "success",
        "cost",
        "findings_open",
        "harness_version",
        "window_days",
    }
    # Both carry their denominator, and the cost fields carry their unit.
    assert {"rate", "n"} <= set(summary["success"])
    assert {"median_usd", "total_usd", "n"} <= set(summary["cost"])
