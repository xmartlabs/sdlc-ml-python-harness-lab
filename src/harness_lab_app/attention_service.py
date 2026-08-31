"""What needs attention — the only part of a home page worth acting on.

The Dashboard answered "what data exists in the system?". This answers "what
should I do next?", which is a different question and a much shorter list.

Three rules, and they are why this is a service rather than a query the frontend
assembles:

- **Every item is actionable.** A number nobody can act on is a statistic and
  belongs in the evidence summary further down the page.
- **Every item is in human language.** No run ids in a headline, no raw backend
  message, no field name. "4 cases regressed" and
  "OperationalError on run_id=8f3a..." are not the same product.
- **Every item carries its own call to action** — a route and a label — so the
  page cannot show a problem without showing the way into it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from harness_lab_app.models import (
    Experiment,
    ExperimentStatus,
    Finding,
    FindingStatus,
    Run,
    RunState,
    Score,
    TargetRepository,
    TargetStatus,
)

# Below this, a comparison is exploratory whatever it is called (ADR-0028).
USEFUL_REPETITIONS = 5
# How far back a failed run is still worth a nudge about.
RECENT_DAYS = 14

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def _item(
    kind: str,
    severity: str,
    headline: str,
    detail: str,
    *,
    action: str,
    route: str,
    count: int = 0,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "count": count,
        "headline": headline,
        "detail": detail,
        "action": action,
        "route": route,
    }


class AttentionService:
    """The home page's action list, computed from the corpus."""

    def __init__(self, session: Session):
        self.session = session

    def build(self) -> dict[str, Any]:
        items = [
            *self._regressions(),
            *self._suggestions(),
            *self._findings_without_experiments(),
            *self._unscored_runs(),
            *self._failed_runs(),
            *self._thin_experiments(),
            *self._repositories_without_gates(),
        ]
        items.sort(
            key=lambda item: (_SEVERITY_RANK.get(item["severity"], 9), -item["count"])
        )
        return {"items": items, "clear": not items}

    # --- individual signals ----------------------------------------------

    def _regressions(self) -> list[dict[str, Any]]:
        from harness_lab_app.leaderboard_service import LeaderboardService
        from harness_lab_app.overview_service import regressions

        found = regressions(LeaderboardService(self.session).list_rows())
        if not found["count"]:
            return []
        cases = ", ".join(found["cases"][:3])
        more = (
            "" if len(found["cases"]) <= 3 else f" and {len(found['cases']) - 3} more"
        )
        return [
            _item(
                "regression",
                "critical",
                f"{found['count']} case{'s' if found['count'] != 1 else ''} regressed",
                f"Passed before, failing now: {cases}{more}.",
                # The suggested-findings queue, because that is where a
                # regression becomes actionable: it arrives with the last
                # passing run and the failing one attached, and accepting it
                # produces a finding that an experiment can settle. A filtered
                # case list would only restate the headline.
                action="Inspect regressions",
                route="/evidence/findings?tab=suggested",
                count=found["count"],
            )
        ]

    def _suggestions(self) -> list[dict[str, Any]]:
        from harness_lab_app.finding_service import FindingService

        pending = FindingService(self.session).suggestions()
        if not pending:
            return []
        return [
            _item(
                "suggested_findings",
                "warning",
                f"{len(pending)} suggested finding{'s' if len(pending) != 1 else ''} "
                "waiting for review",
                "The lab noticed these patterns. None of them is a finding until "
                "someone accepts it.",
                action="Review suggestions",
                route="/evidence/findings?tab=suggested",
                count=len(pending),
            )
        ]

    def _findings_without_experiments(self) -> list[dict[str, Any]]:
        open_findings = [
            finding
            for finding in self.session.exec(select(Finding)).all()
            if finding.status in (FindingStatus.open, FindingStatus.proposed)
            and not finding.experiment_id
            and finding.recommendation.strip()
        ]
        if not open_findings:
            return []
        return [
            _item(
                "untested_findings",
                "warning",
                f"{len(open_findings)} finding{'s' if len(open_findings) != 1 else ''} "
                "with nothing testing them",
                "Each one recommends a change that no experiment has measured.",
                action="Turn a finding into an experiment",
                route="/evidence/findings",
                count=len(open_findings),
            )
        ]

    def _unscored_runs(self) -> list[dict[str, Any]]:
        scored = {score.run_id for score in self.session.exec(select(Score)).all()}
        finished = [
            run
            for run in self.session.exec(select(Run)).all()
            if run.state == RunState.completed and run.run_id not in scored
        ]
        if not finished:
            return []
        return [
            _item(
                "unscored_runs",
                "info",
                f"{len(finished)} finished run{'s' if len(finished) != 1 else ''} "
                "have no scores",
                "Scoring is on demand, so these runs are absent from every "
                "aggregate on this page.",
                action="Score the backlog",
                route="/advanced/runs?filter=unscored",
                count=len(finished),
            )
        ]

    def _failed_runs(self) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(days=RECENT_DAYS)
        failed = [
            run
            for run in self.session.exec(select(Run)).all()
            if run.state == RunState.failed
            and run.created_at.replace(tzinfo=UTC) >= cutoff
            and run.experiment_id
        ]
        if not failed:
            return []
        experiments = sorted({run.experiment_id or "" for run in failed})
        return [
            _item(
                "failed_experiment_runs",
                "critical",
                f"{len(failed)} experiment run{'s' if len(failed) != 1 else ''} failed",
                (
                    f"Affecting {len(experiments)} experiment(s). A failed arm "
                    "leaves the comparison unbalanced — an arm with fewer "
                    "completed runs is not the same measurement as its control."
                ),
                action="Inspect failures",
                route="/advanced/runs?filter=failed",
                count=len(failed),
            )
        ]

    def _thin_experiments(self) -> list[dict[str, Any]]:
        thin = [
            experiment
            for experiment in self.session.exec(select(Experiment)).all()
            if experiment.status
            in (ExperimentStatus.running, ExperimentStatus.complete)
            and min([experiment.repetitions, *experiment.case_repetitions.values()])
            < USEFUL_REPETITIONS
        ]
        if not thin:
            return []
        return [
            _item(
                "thin_experiments",
                "warning",
                f"{len(thin)} experiment{'s' if len(thin) != 1 else ''} cannot "
                "support a conclusion yet",
                (
                    f"Fewer than {USEFUL_REPETITIONS} repetitions per arm. Agents "
                    "are stochastic; these are exploratory until they have more."
                ),
                action="Add repetitions",
                route="/evaluate/experiments",
                count=len(thin),
            )
        ]

    def _repositories_without_gates(self) -> list[dict[str, Any]]:
        bare = [
            target
            for target in self.session.exec(select(TargetRepository)).all()
            if target.status == TargetStatus.active and not target.validation_commands
        ]
        if not bare:
            return []
        return [
            _item(
                "repositories_without_gates",
                "warning",
                f"{len(bare)} repositor{'ies' if len(bare) != 1 else 'y'} have no "
                "validation gate",
                (
                    "Without the repository's own tests, a run against it can "
                    "only ever be scored by hand — so it cannot enter any "
                    "success rate."
                ),
                action="Configure validation",
                route="/evidence/repositories",
                count=len(bare),
            )
        ]


def evidence_summary(session: Session) -> dict[str, Any]:
    """The five or six numbers a home page may show, and no more (§17).

    Placed *after* the workflows on purpose. This is context for someone who
    already knows what they came to do — not the reason to come. The full
    leaderboard, the model filters and the official benchmark references live
    in Experiments, Advanced Compare and Benchmarks, where a reader has a
    question that needs them.
    """
    from harness_lab_app.models import Case, CaseStatus
    from harness_lab_app.overview_service import OverviewService

    overview = OverviewService(session).build()
    cases = list(session.exec(select(Case)).all())
    targets = list(session.exec(select(TargetRepository)).all())
    experiments = list(session.exec(select(Experiment)).all())

    return {
        "experiments_active": sum(
            1 for row in experiments if row.status == ExperimentStatus.running
        ),
        "repositories": sum(1 for row in targets if row.status == TargetStatus.active),
        "cases": sum(1 for row in cases if row.status == CaseStatus.active),
        # Carries its own denominator: a rate over four runs and one over four
        # hundred are different claims.
        "success": overview["success"],
        "cost": overview["cost"],
        "findings_open": overview["findings"]["open"],
        "harness_version": overview["harness_version"],
        "window_days": overview["window_days"],
    }
