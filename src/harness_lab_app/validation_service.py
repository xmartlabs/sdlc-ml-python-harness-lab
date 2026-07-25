"""Orchestrates objective validation and multidimensional scoring."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.case_service import CaseService
from harness_lab_app.config import get_settings
from harness_lab_app.executors import SubprocessRunExecutor, get_executor
from harness_lab_app.models import Run, RunEvent, Validation, utc_now
from harness_lab_app.run_service import RunService
from harness_lab_app.validation import (
    build_validation_spec,
    collect_cost,
    collect_effort,
    compute_composite,
    parse_objective,
)

if TYPE_CHECKING:
    from harness_lab_app.executors import RunExecutor


class ValidationService:
    def __init__(self, session: Session, executor: RunExecutor | None = None):
        self.session = session
        self._executor = executor

    def _executor_instance(self) -> RunExecutor:
        return self._executor if self._executor is not None else get_executor()

    def get_validation(self, run_id: str) -> Validation | None:
        return self.session.exec(
            select(Validation).where(Validation.run_id == run_id)
        ).first()

    def _upsert(self, run_id: str) -> Validation:
        validation = self.get_validation(run_id)
        if validation is None:
            validation = Validation(run_id=run_id)
            self.session.add(validation)
        return validation

    def _run_dir(self, run_id: str):
        return get_settings().data_runs_dir / run_id

    def _run_objective(self, spec: dict[str, Any], run_id: str) -> dict[str, Any]:
        # "local" specs (SWE-bench Pro) shell out to docker themselves; run them
        # in this process — the worker has the socket, validation containers
        # deliberately do not.
        executor: RunExecutor = (
            SubprocessRunExecutor() if spec.get("local") else self._executor_instance()
        )
        results: list[dict[str, Any]] = []
        for command in spec["commands"]:
            label = command[-1] if command else ""
            try:
                handle = executor.start(f"{run_id}-validate", command)
                output = "\n".join(message for _level, message in handle.iter_lines())
                exit_code = handle.wait()
            except Exception as exc:  # record the failure, never crash the run
                results.append(
                    {
                        "command": label,
                        "exit_code": -1,
                        "output": f"executor error: {exc!r}",
                    }
                )
                continue
            results.append({"command": label, "exit_code": exit_code, "output": output})
        return parse_objective(results, spec["strategy"])

    def validate_run(self, run_id: str) -> Validation:
        """Run objective validation and record cost + effort (subjective stays)."""
        services = RunService(self.session)
        run = services.get_run(run_id)
        config = services.get_run_config(run.run_config_id)
        case = CaseService(self.session).get_case(config.case_id)

        spec = build_validation_spec(case, run_id)
        self.session.add(
            RunEvent(
                run_id=run_id,
                level="info",
                message=f"Objective validation ({spec['strategy']}): "
                f"{len(spec['commands'])} command(s)",
            )
        )
        objective = self._run_objective(spec, run_id)
        cost = collect_cost(self._run_dir(run_id))
        effort = collect_effort(self._run_dir(run_id), run.interventions)

        validation = self._upsert(run_id)
        validation.objective = objective
        validation.cost = cost
        validation.effort = effort
        validation.composite = compute_composite(objective, validation.subjective)
        validation.updated_at = utc_now()
        self.session.add(validation)
        self.session.add(
            RunEvent(
                run_id=run_id,
                level="info" if objective.get("passed") is not False else "error",
                message=f"Objective validation: {objective.get('summary', 'done')}",
            )
        )
        self.session.commit()
        self.session.refresh(validation)
        return validation

    def apply_subjective(
        self,
        run_id: str,
        functional_score: int,
        interventions: int,
        rubric: dict[str, int] | None = None,
        notes: str = "",
        *,
        score_source: str = "manual",
    ) -> Validation:
        """Record the subjective score and recompute the composite.

        ``score_source`` records how the score was produced — ``manual`` (a human
        entered it), ``objective`` (derived from the automated test verdict), or
        ``llm_judge`` (an LLM graded the run) — so the UI can label it.
        """
        validation = self._upsert(run_id)
        validation.subjective = {
            "functional_score": functional_score,
            "rubric": rubric or {},
            "notes": notes,
            "score_source": score_source,
        }
        # Refresh effort with the confirmed intervention count.
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is not None:
            validation.effort = collect_effort(self._run_dir(run_id), interventions)
        validation.composite = compute_composite(
            validation.objective, validation.subjective
        )
        validation.updated_at = utc_now()
        self.session.add(validation)
        self.session.commit()
        self.session.refresh(validation)
        return validation
