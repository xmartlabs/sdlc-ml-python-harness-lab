"""The Overview's definitions, which a chart would otherwise only imply.

Each of these pins down a judgement that is easy to get subtly wrong and
impossible to notice afterwards: what counts as a regression, what a missing
week means, and which runs are allowed into a rate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from harness_lab_app.overview_service import (
    cost_summary,
    quality_trend,
    regressions,
    success_rate,
)


def _row(**over):
    base = {
        "run_id": "r",
        "case_id": "c1",
        "resolved": True,
        "cost_usd": 1.0,
        "chain_depth": 0,
        "created_at": datetime.now(UTC).isoformat(),
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# Success rate
# --------------------------------------------------------------------------


def test_a_run_with_no_objective_verdict_is_unknown_not_a_failure():
    result = success_rate(
        [_row(resolved=True), _row(resolved=False), _row(resolved=None)]
    )
    assert result["n"] == 2
    assert result["rate"] == 0.5


def test_a_rate_over_too_few_runs_says_so():
    assert "indicative" in success_rate([_row(), _row()])["note"]


def test_a_rate_over_enough_runs_needs_no_caveat():
    assert success_rate([_row() for _ in range(6)])["note"] == ""


def test_no_validated_runs_yields_no_rate_rather_than_zero():
    result = success_rate([_row(resolved=None)])
    assert result["rate"] is None
    assert result["n"] == 0


# --------------------------------------------------------------------------
# Regressions — the narrow definition is the point
# --------------------------------------------------------------------------


def test_a_regression_is_something_we_had_and_lost():
    result = regressions(
        [
            _row(case_id="c1", resolved=True, created_at="2026-01-01T00:00:00+00:00"),
            _row(case_id="c1", resolved=False, created_at="2026-02-01T00:00:00+00:00"),
        ]
    )
    assert result["count"] == 1
    assert result["cases"] == ["c1"]


def test_a_case_that_never_passed_is_an_open_problem_not_a_regression():
    result = regressions(
        [
            _row(case_id="c1", resolved=False, created_at="2026-01-01T00:00:00+00:00"),
            _row(case_id="c1", resolved=False, created_at="2026-02-01T00:00:00+00:00"),
        ]
    )
    assert result["count"] == 0
    assert result["eligible_cases"] == 1


def test_a_case_with_one_run_cannot_regress():
    result = regressions([_row(case_id="c1", resolved=True)])
    assert result["eligible_cases"] == 0
    assert result["rate"] is None


def test_a_case_that_recovered_is_not_counted():
    result = regressions(
        [
            _row(case_id="c1", resolved=True, created_at="2026-01-01T00:00:00+00:00"),
            _row(case_id="c1", resolved=False, created_at="2026-02-01T00:00:00+00:00"),
            _row(case_id="c1", resolved=True, created_at="2026-03-01T00:00:00+00:00"),
        ]
    )
    assert result["count"] == 0


def test_a_human_steered_continuation_does_not_describe_the_harness():
    result = regressions(
        [
            _row(case_id="c1", resolved=True, created_at="2026-01-01T00:00:00+00:00"),
            _row(
                case_id="c1",
                resolved=False,
                chain_depth=1,
                created_at="2026-02-01T00:00:00+00:00",
            ),
        ]
    )
    assert result["eligible_cases"] == 0


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------


def test_cost_uses_the_median_so_one_runaway_run_cannot_distort_it():
    result = cost_summary(
        [
            _row(cost_usd=1.0),
            _row(cost_usd=1.0),
            _row(cost_usd=1.0),
            _row(cost_usd=97.0),
        ]
    )
    assert result["median_usd"] == 1.0


def test_cost_per_success_is_the_figure_a_decision_turns_on():
    result = cost_summary(
        [
            _row(cost_usd=1.0, resolved=True),
            _row(cost_usd=1.0, resolved=False),
            _row(cost_usd=2.0, resolved=False),
        ]
    )
    # $4 spent, one success.
    assert result["cost_per_success"] == pytest.approx(4.0)


def test_no_success_means_no_cost_per_success_rather_than_infinity():
    assert cost_summary([_row(resolved=False)])["cost_per_success"] is None


def test_runs_without_a_recorded_cost_are_left_out_of_the_median():
    result = cost_summary([_row(cost_usd=2.0), _row(cost_usd=None)])
    assert result["n"] == 1
    assert result["median_usd"] == 2.0


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------


def test_a_quiet_week_reports_nothing_rather_than_zero_percent():
    """A gap drawn as a collapse to zero tells the opposite of the truth."""
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    buckets = quality_trend([_row(created_at=old)], weeks=4)
    assert all(bucket["rate"] is None and bucket["n"] == 0 for bucket in buckets)


def test_the_trend_is_ordered_oldest_first():
    buckets = quality_trend([], weeks=4)
    assert len(buckets) == 4
    assert [b["week_ending"] for b in buckets] == sorted(
        b["week_ending"] for b in buckets
    )


def test_a_recent_run_lands_in_the_latest_bucket():
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    buckets = quality_trend([_row(created_at=recent, resolved=True)], weeks=4)
    assert buckets[-1]["n"] == 1
    assert buckets[-1]["rate"] == 1.0


# --------------------------------------------------------------------------
# The score corpus reaches the dashboard
# --------------------------------------------------------------------------


def test_the_score_corpus_is_aggregated_with_direction_and_provenance(session):
    """A score recorded and never read outside its own run is bookkeeping."""
    from harness_lab_app.models import Score
    from harness_lab_app.score_service import ScoreService

    for index, adherence in enumerate([0.2, 0.4, 0.6]):
        session.add(
            Score(
                run_id=f"r{index}",
                name="skill_adherence",
                version="v1",
                category="behavioral",
                provenance="judgement",
                value_num=adherence,
            )
        )
    session.add(
        Score(
            run_id="r0",
            name="task_validation_pass",
            version="v1",
            category="outcome",
            provenance="fact",
            value_bool=True,
        )
    )
    session.commit()

    aggregate = ScoreService(session).aggregate()
    by_name = {metric["name"]: metric for metric in aggregate["metrics"]}

    assert aggregate["runs"] == 3
    assert by_name["skill_adherence"]["mean"] == pytest.approx(0.4)
    assert by_name["skill_adherence"]["direction"] == "higher_is_better"
    assert by_name["skill_adherence"]["provenance"] == "judgement"


def test_a_pass_fail_is_summarised_as_a_rate_not_averaged_as_one_and_zero(session):
    from harness_lab_app.models import Score
    from harness_lab_app.score_service import ScoreService

    for index, passed in enumerate([True, True, False, False]):
        session.add(
            Score(
                run_id=f"r{index}",
                name="task_validation_pass",
                version="v1",
                category="outcome",
                provenance="fact",
                value_bool=passed,
            )
        )
    session.commit()

    (metric,) = ScoreService(session).aggregate()["metrics"]
    assert metric["true_rate"] == 0.5
    # It is not folded into the numeric aggregate alongside costs and counts.
    assert metric["mean"] is None


def test_versions_are_aggregated_apart(session):
    """A rubric change must not pool two different quantities."""
    from harness_lab_app.models import Score
    from harness_lab_app.score_service import ScoreService

    session.add(Score(run_id="r1", name="skill_adherence", version="v1", value_num=0.2))
    session.add(Score(run_id="r2", name="skill_adherence", version="v2", value_num=0.9))
    session.commit()

    metrics = ScoreService(session).aggregate()["metrics"]
    assert {(m["name"], m["version"]) for m in metrics} == {
        ("skill_adherence", "v1"),
        ("skill_adherence", "v2"),
    }
