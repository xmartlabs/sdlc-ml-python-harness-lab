"""Assemble a run's evaluation context, run a profile, and persist the scores."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from harness_lab_app import trajectory as trajectory_module
from harness_lab_app.attribution import read_step_events, read_step_logs
from harness_lab_app.attribution_service import AttributionService
from harness_lab_app.case_service import CaseService
from harness_lab_app.config import get_settings
from harness_lab_app.evaluation import EvaluationContext, run_profile
from harness_lab_app.models import Audit, Score, Validation
from harness_lab_app.run_service import RunService


class ScoreService:
    def __init__(self, session: Session):
        self.session = session

    def list_scores(self, run_id: str) -> list[Score]:
        rows = list(
            self.session.exec(select(Score).where(Score.run_id == run_id)).all()
        )
        # Category first so a reader never meets a judgement before the facts.
        order = {"outcome": 0, "efficiency": 1, "trajectory": 2, "behavioral": 3}
        return sorted(rows, key=lambda row: (order.get(row.category, 9), row.name))

    def aggregate(self, run_ids: list[str] | None = None) -> dict[str, Any]:
        """Aggregate the score corpus, one entry per definition.

        This is what the registry was for. A score recorded and never read
        anywhere but its own run detail is bookkeeping; aggregated across the
        corpus it answers the questions a harness engineer actually has — is
        adherence rising, which items never earn their keep, what does a success
        cost.

        Every entry carries its **direction** and **provenance**, so a consumer
        can colour an improvement correctly and can refuse to average a
        judgement with a fact.
        """
        from harness_lab_app.scores import REGISTRY
        from harness_lab_app.statistics import aggregate_values

        statement = select(Score)
        if run_ids is not None:
            if not run_ids:
                return {"metrics": [], "runs": 0}
            statement = statement.where(Score.run_id.in_(run_ids))  # type: ignore[attr-defined]
        rows = list(self.session.exec(statement).all())

        grouped: dict[tuple[str, str], list[Score]] = {}
        for row in rows:
            grouped.setdefault((row.name, row.version), []).append(row)

        metrics = []
        for (name, version), scores in sorted(grouped.items()):
            definition = REGISTRY.get(name)
            numeric = [
                score.value_num for score in scores if score.value_num is not None
            ]
            booleans = [
                score.value_bool for score in scores if score.value_bool is not None
            ]
            aggregate = aggregate_values(name, numeric)
            metrics.append(
                {
                    "name": name,
                    "version": version,
                    "category": definition.category if definition else "",
                    "provenance": definition.provenance if definition else "",
                    "direction": definition.direction if definition else "neutral",
                    "description": definition.description if definition else "",
                    "runs": len({score.run_id for score in scores}),
                    "mean": aggregate.mean,
                    "median": aggregate.median,
                    "stdev": aggregate.stdev,
                    "minimum": aggregate.minimum,
                    "maximum": aggregate.maximum,
                    # A pass/fail is summarised as a rate, never averaged as 1/0
                    # alongside numeric metrics.
                    "true_rate": (
                        sum(1 for value in booleans if value) / len(booleans)
                        if booleans
                        else None
                    ),
                }
            )
        return {"metrics": metrics, "runs": len({row.run_id for row in rows})}

    def build_context(self, run_id: str) -> EvaluationContext:
        runs = RunService(self.session)
        run = runs.get_run(run_id)
        config = runs.get_run_config(run.run_config_id)

        validation = self.session.exec(
            select(Validation).where(Validation.run_id == run_id)
        ).first()
        audit = self.session.exec(select(Audit).where(Audit.run_id == run_id)).first()

        run_dir = get_settings().data_runs_dir / run_id
        diff_path = run_dir / "diff.patch"
        diff_text = ""
        if diff_path.is_file():
            diff_text = diff_path.read_text(errors="replace")

        expected: tuple[str, ...] = ()
        forbidden: tuple[str, ...] = ()
        task = ""
        expected_tools: tuple[str, ...] = ()
        try:
            case = CaseService(self.session).get_case(config.case_id)
            expected = tuple(case.files_expected)
            forbidden = tuple(case.files_forbidden)
            task = case.prompt_variants.get(config.prompt_variant) or case.description
            expected_tools = trajectory_module.expected_tools_for(
                files_expected=expected,
                validation_commands=tuple(case.validation_commands),
            )
        except ValueError:
            # A run whose case was deleted still has facts worth scoring; only
            # the surface checks drop out.
            pass

        # The harness-behaviour evaluator reads read-coverage off attribution,
        # and that coverage is only meaningful against the run's own governance
        # surface — which is what AttributionService resolves (ADR-0031).
        attribution = AttributionService(self.session).get_attribution(run_id)

        event_lines, _ = read_step_events(run_dir)
        log_text, _ = read_step_logs(run_dir)

        return EvaluationContext(
            run_id=run_id,
            validation=(
                {
                    "objective": validation.objective,
                    "cost": validation.cost,
                    "effort": validation.effort,
                }
                if validation
                else None
            ),
            audit_findings=audit.findings_json if audit else None,
            attribution=dict(attribution.harness_usage) if attribution else None,
            trajectory=trajectory_module.build(
                event_lines=event_lines,
                log_text=log_text,
                task=task,
                expected_tools=expected_tools,
            ),
            diff_text=diff_text,
            files_expected=expected,
            files_forbidden=forbidden,
        )

    def evaluate_run(self, run_id: str, profile: str = "default") -> list[Score]:
        """Score a run, replacing any previous values for the same definitions.

        Re-scoring overwrites in place rather than appending: two values for one
        ``(run, name, version)`` would be a contradiction, not a history. A
        genuine change of method gets a new ``version`` in the registry, which is
        the whole reason versions are there.
        """
        context = self.build_context(run_id)
        rows = [value.as_row() for value in run_profile(profile, context)]

        existing = {
            (row.name, row.version): row
            for row in self.session.exec(
                select(Score).where(Score.run_id == run_id)
            ).all()
        }
        stored: list[Score] = []
        for row in rows:
            key = (row["name"], row["version"])
            score = existing.get(key) or Score(run_id=run_id, **row)
            for field, value in row.items():
                setattr(score, field, value)
            self.session.add(score)
            stored.append(score)
        self.session.commit()
        for score in stored:
            self.session.refresh(score)
        return stored
