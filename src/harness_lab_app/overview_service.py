"""The Overview: what the lab knows about itself, right now.

The Dashboard used to lead with operational detail — how many runs are pending,
how many failed. That answers "is the machine busy?", which is the least
interesting question the lab can answer once it has a run corpus.

This computes the questions worth leading with instead: is the harness getting
better, is anything regressing, what does a success cost, and how much of the
corpus is actually re-runnable.

Every figure carries its **denominator**. A success rate over four runs and one
over four hundred are different claims, and a number rendered without its N
invites the reader to treat them the same.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from harness_lab_app.leaderboard_service import LeaderboardService
from harness_lab_app.models import (
    Case,
    CaseStatus,
    Experiment,
    Finding,
    FindingStatus,
    Score,
)

# How far back "recent" reaches. A month is short enough that the number moves
# when the harness does, and long enough to contain more than one afternoon.
WINDOW_DAYS = 30
TREND_WEEKS = 8

# Below this many objectively-validated runs, a rate is noise wearing a
# percentage sign. Reported, but labelled.
MIN_RATE_SAMPLE = 5


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def success_rate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of objectively-validated runs that passed.

    Manual-score runs are excluded rather than counted as failures: a run with
    no objective verdict is *unknown*, and folding it in either direction
    invents a result.
    """
    verdicts = [row["resolved"] for row in rows if row.get("resolved") is not None]
    if not verdicts:
        return {"rate": None, "n": 0, "note": "No objectively-validated runs yet."}
    rate = sum(1 for verdict in verdicts if verdict) / len(verdicts)
    note = ""
    if len(verdicts) < MIN_RATE_SAMPLE:
        note = (
            f"Only {len(verdicts)} validated run(s) — this rate is indicative, "
            "not a measurement."
        )
    return {"rate": rate, "n": len(verdicts), "note": note}


def regressions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cases whose latest verdict is a failure after an earlier success.

    Deliberately narrow. A case that has only ever failed is not a regression,
    it is an open problem; a case that never had an objective verdict cannot
    regress at all. Counting either would inflate the number that is supposed to
    mean "something we had, and lost".
    """
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("resolved") is None:
            continue
        # A continued run was steered by a human between steps, so it does not
        # describe the harness's own behaviour on the case.
        if row.get("chain_depth"):
            continue
        by_case[row["case_id"]].append(row)

    regressed: list[str] = []
    eligible = 0
    for case_id, case_rows in by_case.items():
        ordered = sorted(case_rows, key=lambda row: row.get("created_at") or "")
        if len(ordered) < 2:
            continue
        eligible += 1
        if not ordered[-1]["resolved"] and any(row["resolved"] for row in ordered[:-1]):
            regressed.append(case_id)

    return {
        "count": len(regressed),
        "eligible_cases": eligible,
        "rate": (len(regressed) / eligible) if eligible else None,
        "cases": sorted(regressed),
    }


def cost_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Median and per-success cost.

    The median, not the mean: one runaway run distorts a mean badly enough to
    make the figure useless, and the cost distribution of agent runs has a long
    right tail by nature.

    ``cost_per_success`` is the figure a business decision actually turns on —
    92% at $4.00 can be a worse buy than 89% at $0.70.
    """
    costs = [
        row["cost_usd"] for row in rows if isinstance(row.get("cost_usd"), (int, float))
    ]
    successes = [
        row
        for row in rows
        if row.get("resolved") is True and isinstance(row.get("cost_usd"), (int, float))
    ]
    total = sum(
        row["cost_usd"] for row in rows if isinstance(row.get("cost_usd"), (int, float))
    )
    return {
        "median_usd": statistics.median(costs) if costs else None,
        "n": len(costs),
        "total_usd": total if costs else None,
        "cost_per_success": (total / len(successes)) if successes else None,
        "successes": len(successes),
    }


def quality_trend(rows: list[dict[str, Any]], weeks: int = TREND_WEEKS) -> list[dict]:
    """Weekly success rate, oldest first.

    A week with no validated run reports ``None`` rather than 0. A chart that
    draws a gap as a collapse to zero tells the opposite of the truth.
    """
    now = datetime.now(UTC)
    buckets: list[dict[str, Any]] = []
    for index in range(weeks - 1, -1, -1):
        end = now - timedelta(days=7 * index)
        start = end - timedelta(days=7)
        verdicts = [
            row["resolved"]
            for row in rows
            if row.get("resolved") is not None
            and (stamp := _parse(row.get("created_at"))) is not None
            and start <= stamp < end
        ]
        buckets.append(
            {
                "week_ending": end.date().isoformat(),
                "n": len(verdicts),
                "rate": (
                    sum(1 for verdict in verdicts if verdict) / len(verdicts)
                    if verdicts
                    else None
                ),
            }
        )
    return buckets


class OverviewService:
    def __init__(self, session: Session):
        self.session = session

    def _score_signal(self) -> dict[str, Any]:
        """The handful of registry metrics worth a headline.

        `skill_adherence` is the one this lab exists to move: it is the share of
        the harness an auditor judged genuinely used. A harness of 24 skills
        that an agent uses two of is not a good harness, and no other figure on
        this page would ever say so.
        """
        from harness_lab_app.score_service import ScoreService

        aggregate = ScoreService(self.session).aggregate()
        by_name = {metric["name"]: metric for metric in aggregate["metrics"]}
        return {
            "runs": aggregate["runs"],
            "adherence": by_name.get("skill_adherence"),
            "useful": by_name.get("harness_items_useful"),
            "redundant": by_name.get("harness_items_redundant"),
            "diff_size": by_name.get("diff_lines_changed"),
            "forbidden": by_name.get("forbidden_files_modified"),
            "metrics": aggregate["metrics"],
        }

    def build(self, window_days: int = WINDOW_DAYS) -> dict[str, Any]:
        rows = LeaderboardService(self.session).list_rows()
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
        recent = [
            row
            for row in rows
            if (stamp := _parse(row.get("created_at"))) is not None and stamp >= cutoff
        ]

        experiments = list(self.session.exec(select(Experiment)).all())
        findings = list(self.session.exec(select(Finding)).all())
        cases = list(self.session.exec(select(Case)).all())
        scored_run_ids = {
            score.run_id for score in self.session.exec(select(Score)).all()
        }
        terminal_run_ids = {row["run_id"] for row in rows}

        # "In the regression suite" means re-runnable without a human: an active
        # case that carries its own objective validation. A case scored by hand
        # cannot gate anything, so counting it would overstate the suite.
        suite = [
            case
            for case in cases
            if case.status == CaseStatus.active and case.validation_commands
        ]

        harness_versions = [
            row["harness_version"]
            for row in sorted(rows, key=lambda row: row.get("created_at") or "")
            if row.get("harness_version")
        ]

        return {
            "window_days": window_days,
            "harness_version": harness_versions[-1] if harness_versions else None,
            "experiments": {
                "total": len(experiments),
                "recent": sum(
                    1
                    for experiment in experiments
                    if experiment.created_at is not None
                    and experiment.created_at.replace(tzinfo=UTC) >= cutoff
                ),
            },
            "success": success_rate(recent),
            "success_all_time": success_rate(rows),
            "regressions": regressions(rows),
            "cost": cost_summary(recent),
            "regression_suite": {
                "cases": len(suite),
                "of_active": sum(
                    1 for case in cases if case.status == CaseStatus.active
                ),
            },
            "findings": {
                "open": sum(
                    1
                    for finding in findings
                    if finding.status in (FindingStatus.open, FindingStatus.proposed)
                ),
                "validated": sum(
                    1
                    for finding in findings
                    if finding.status == FindingStatus.validated
                ),
                "total": len(findings),
            },
            # Scoring is on demand, so the gap between "runs that finished" and
            # "runs with scores" is a real backlog worth surfacing rather than
            # a number to quietly omit.
            "scoring": {
                "scored": len(scored_run_ids & terminal_run_ids),
                "terminal": len(terminal_run_ids),
            },
            "trend": quality_trend(rows),
            # The score corpus, aggregated. Distinct from the figures above,
            # which are computed from `Validation`: these are the registry's
            # own metrics, carrying their direction and provenance so a consumer
            # can colour them correctly and keep judgements apart from facts.
            "scores": self._score_signal(),
            "runs": {"total": len(rows), "recent": len(recent)},
        }
