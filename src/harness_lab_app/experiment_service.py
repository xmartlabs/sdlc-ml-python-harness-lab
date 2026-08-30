"""Experiments: a declared question, a validated design, and the runs under it.

A batch already produced N runs from a cross-product. What it could not do was
say what those runs were *for*, or refuse a design that cannot answer the
question it claims to. That is the whole of the difference here (ADR-0033):

- the question is stated before anything executes;
- the arms are **declared**, not recovered afterwards from a command string;
- the mode ([ADR-0028](../../docs/adr/0028-evaluation-modes.md)) is checked
  against the design, so a comparison the design does not support is rejected at
  creation instead of being rendered as though it were sound.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.models import (
    ConditionRole,
    Experiment,
    ExperimentCondition,
    ExperimentMode,
    ExperimentStatus,
    Run,
)
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate

if TYPE_CHECKING:
    from harness_lab_app.schemas import ExperimentCreate

# Axis fields a condition may fix. Anything else is either shared by the whole
# experiment (cases, prompt variants, repetitions) or not an axis at all.
CONDITION_FIELDS = frozenset(
    {
        "harness",
        "executor_model",
        "planner_model",
        "target_repo_id",
        "target_ref",
        "harness_source",
        "skills_disabled",
        "skills_extra",
        "max_steps",
    }
)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class ExperimentError(ValueError):
    """An experiment could not be created or launched as asked."""


class ExperimentNotFoundError(ExperimentError):
    """No experiment with that id."""


class ExperimentDesignError(ExperimentError):
    """The design contradicts the mode it declares."""


def _varying_fields(conditions: list[dict[str, Any]]) -> set[str]:
    """Which axis fields actually differ between the arms."""
    varying: set[str] = set()
    for field in CONDITION_FIELDS:
        seen = {
            _hashable(condition.get("overrides", {}).get(field))
            for condition in conditions
        }
        if len(seen) > 1:
            varying.add(field)
    return varying


def _hashable(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(sorted(str(item) for item in value))
    return value


def validate_design(
    mode: ExperimentMode, conditions: list[dict[str, Any]]
) -> list[str]:
    """Reject a design that cannot answer the question its mode implies.

    This is where [ADR-0028](../../docs/adr/0028-evaluation-modes.md) stops being
    documentation. A cross-product varying both the harness and the repository is
    a perfectly valid *request* and an invalid *experiment*: whatever difference
    it produces cannot be attributed to either factor. Catching it here is the
    difference between a lab and a job runner that renders averages.

    Returns warnings that do not invalidate the design; raises when it does.
    """
    warnings: list[str] = []
    if not conditions:
        raise ExperimentDesignError("An experiment needs at least one condition.")

    labels = [condition["label"] for condition in conditions]
    if len(set(labels)) != len(labels):
        raise ExperimentDesignError("Condition labels must be unique.")

    if mode == ExperimentMode.discovery:
        # No control, no causal claim, nothing to invalidate — the label on the
        # output is the safeguard, not the design.
        return warnings

    varying = _varying_fields(conditions)
    controls = [c for c in conditions if c.get("role") == ConditionRole.control]

    if len(conditions) > 1 and len(controls) != 1:
        raise ExperimentDesignError(
            "A comparison needs exactly one control condition — mark the arm the "
            f"others are measured against (found {len(controls)})."
        )

    if mode == ExperimentMode.harness_evaluation:
        if "target_repo_id" in varying:
            raise ExperimentDesignError(
                "Mode 'harness_evaluation' holds the repository fixed and varies "
                "the harness. These conditions vary the repository as well, so a "
                "difference between them cannot be attributed to the harness. Use "
                "mode 'cross_repo', or give every condition the same target."
            )
        if "executor_model" in varying:
            raise ExperimentDesignError(
                "Mode 'harness_evaluation' holds the model fixed. These conditions "
                "vary the executor model, so harness and model effects are "
                "confounded."
            )
        harness_axes = varying & {
            "harness_source",
            "skills_disabled",
            "skills_extra",
            "harness",
        }
        if len(conditions) > 1 and not harness_axes:
            raise ExperimentDesignError(
                "Mode 'harness_evaluation' must vary something about the harness. "
                "These conditions are identical on every harness axis, so there is "
                "nothing to attribute a difference to."
            )
        if len(harness_axes) > 1:
            warnings.append(
                "More than one harness factor varies at once "
                f"({', '.join(sorted(harness_axes))}). A difference cannot be "
                "attributed to either — vary one factor per experiment."
            )

    if mode == ExperimentMode.cross_repo:
        if "target_repo_id" not in varying:
            raise ExperimentDesignError(
                "Mode 'cross_repo' varies the repository. These conditions all "
                "target the same one."
            )
        confounds = varying - {"target_repo_id", "target_ref"}
        if confounds:
            raise ExperimentDesignError(
                "Mode 'cross_repo' holds the harness and model fixed and varies "
                f"only the repository. These also vary: {', '.join(sorted(confounds))}."
            )
        warnings.append(
            "Scores from different repositories are not comparable as though "
            "conditions were identical. Read this experiment per repository — "
            "where the harness holds and where it degrades — never pooled."
        )

    return warnings


def repetition_warning(repetitions: int) -> str | None:
    """State plainly what a given N does and does not support."""
    if repetitions < 2:
        return (
            "1 repetition — exploratory only. Agents are stochastic; a single "
            "trial per arm cannot separate a real difference from run-to-run "
            "variance."
        )
    if repetitions < 5:
        return (
            f"{repetitions} repetitions — exploratory comparison. Use 5 or more "
            "before treating a delta as a finding."
        )
    return None


class ExperimentService:
    def __init__(self, session: Session):
        self.session = session

    def list_experiments(self) -> list[Experiment]:
        rows = list(self.session.exec(select(Experiment)).all())
        return sorted(rows, key=lambda row: row.created_at, reverse=True)

    def get_experiment(self, experiment_id: str) -> Experiment:
        row = self.session.exec(
            select(Experiment).where(Experiment.experiment_id == experiment_id)
        ).first()
        if row is None:
            raise ExperimentNotFoundError(f"Unknown experiment: {experiment_id}")
        return row

    def conditions(self, experiment_id: str) -> list[ExperimentCondition]:
        rows = list(
            self.session.exec(
                select(ExperimentCondition).where(
                    ExperimentCondition.experiment_id == experiment_id
                )
            ).all()
        )
        # Control first: it is the thing everything else is read against.
        return sorted(
            rows, key=lambda row: (row.role != ConditionRole.control, row.label)
        )

    def runs(self, experiment_id: str) -> list[Run]:
        return list(
            self.session.exec(
                select(Run).where(Run.experiment_id == experiment_id)
            ).all()
        )

    def summarize(self, experiment_id: str) -> dict[str, Any]:
        """Aggregate the experiment's runs per declared arm.

        Control first, and the per-case matrix alongside the aggregates: an
        average of "+8 points" can be "fixed four cases, broke two", and only
        the matrix says which.
        """
        from harness_lab_app.score_service import ScoreService
        from harness_lab_app.scores import REGISTRY
        from harness_lab_app.statistics import (
            compare,
            paired_by_case,
            success_delta,
            summarize_condition,
        )

        experiment = self.get_experiment(experiment_id)
        conditions = self.conditions(experiment_id)
        runs = self.runs(experiment_id)
        scores_service = ScoreService(self.session)
        run_service = RunService(self.session)

        per_condition: dict[str, list[dict[str, Any]]] = {
            condition.label: [] for condition in conditions
        }
        verdicts: dict[str, dict[str, bool | None]] = {
            condition.label: {} for condition in conditions
        }
        for run in runs:
            label = run.condition_label or ""
            if label not in per_condition:
                continue
            values: dict[str, Any] = {}
            for score in scores_service.list_scores(run.run_id):
                values[score.name] = (
                    score.value_bool
                    if score.value_bool is not None
                    else score.value_num
                )
            per_condition[label].append(values)
            case_id = run_service.get_run_config(run.run_config_id).case_id
            verdicts[label][case_id] = values.get("task_validation_pass")

        summaries = {
            condition.label: summarize_condition(
                condition.label, per_condition[condition.label]
            )
            for condition in conditions
        }
        directions = {
            name: definition.direction for name, definition in REGISTRY.items()
        }

        control = next((c for c in conditions if c.role == ConditionRole.control), None)
        comparisons = []
        if control is not None:
            for condition in conditions:
                if condition.label == control.label:
                    continue
                comparisons.append(
                    {
                        "control": control.label,
                        "treatment": condition.label,
                        "deltas": [
                            delta.__dict__
                            for delta in compare(
                                summaries[control.label],
                                summaries[condition.label],
                                directions,
                            )
                        ],
                        "success": success_delta(
                            summaries[control.label], summaries[condition.label]
                        ),
                        "case_matrix": paired_by_case(
                            verdicts[control.label], verdicts[condition.label]
                        ),
                    }
                )

        return {
            "experiment_id": experiment_id,
            "question": experiment.question,
            "mode": experiment.mode,
            "repetitions": experiment.repetitions,
            "conditions": [
                {
                    "label": summary.label,
                    "role": next(
                        c.role for c in conditions if c.label == summary.label
                    ),
                    "runs": summary.runs,
                    "scored_runs": summary.scored_runs,
                    "success_rate": summary.success_rate,
                    "warnings": summary.warnings,
                    "metrics": {
                        name: aggregate.__dict__
                        for name, aggregate in summary.metrics.items()
                    },
                }
                for summary in summaries.values()
            ],
            "comparisons": comparisons,
        }

    def create_experiment(self, data: ExperimentCreate) -> tuple[Experiment, list[str]]:
        experiment_id = data.experiment_id.strip()
        if not _SLUG.match(experiment_id):
            raise ExperimentError(
                "experiment_id must be a lowercase slug (a-z, 0-9, dashes), "
                f"2-64 characters — got {experiment_id!r}."
            )
        if not data.question.strip():
            raise ExperimentError(
                "An experiment needs a question. Without one it is a batch, and "
                "calling it an experiment lends it authority it has not earned."
            )
        existing = self.session.exec(
            select(Experiment).where(Experiment.experiment_id == experiment_id)
        ).first()
        if existing is not None:
            raise ExperimentError(f"Experiment already exists: {experiment_id}")

        conditions = [condition.model_dump() for condition in data.conditions]
        for condition in conditions:
            unknown = sorted(set(condition["overrides"]) - CONDITION_FIELDS)
            if unknown:
                raise ExperimentError(
                    f"Condition '{condition['label']}' fixes unknown axis field(s): "
                    f"{', '.join(unknown)}. Valid: {', '.join(sorted(CONDITION_FIELDS))}."
                )
        warnings = validate_design(data.mode, conditions)
        note = repetition_warning(data.repetitions)
        if note:
            warnings.append(note)

        experiment = Experiment(
            experiment_id=experiment_id,
            name=data.name or experiment_id,
            question=data.question.strip(),
            mode=data.mode,
            case_ids=list(data.case_ids),
            prompt_variants=list(data.prompt_variants),
            repetitions=data.repetitions,
            notes=data.notes,
        )
        self.session.add(experiment)
        for condition in conditions:
            self.session.add(
                ExperimentCondition(
                    experiment_id=experiment_id,
                    label=condition["label"],
                    role=condition["role"],
                    description=condition.get("description", ""),
                    overrides=condition["overrides"],
                )
            )
        self.session.commit()
        self.session.refresh(experiment)
        return experiment, warnings

    def launch(self, experiment_id: str, base: RunConfigCreate) -> list[Run]:
        """Expand the declared design into runs, tagged with their arm.

        ``base`` carries the settings every arm shares (score mode, dry run, the
        model when the design does not vary it). A condition's overrides are
        applied on top, so the arm is exactly what was declared.
        """
        experiment = self.get_experiment(experiment_id)
        conditions = self.conditions(experiment_id)
        if not conditions:
            raise ExperimentError(f"Experiment '{experiment_id}' has no conditions.")
        if not experiment.case_ids:
            raise ExperimentError(f"Experiment '{experiment_id}' has no cases.")

        runs_service = RunService(self.session)
        configs: list[tuple[str, RunConfigCreate]] = []
        for case_id in experiment.case_ids:
            for variant in experiment.prompt_variants or [base.prompt_variant]:
                for condition in conditions:
                    payload = base.model_dump()
                    payload.update(
                        {"case_id": case_id, "prompt_variant": variant, "resume": False}
                    )
                    payload.update(condition.overrides)
                    config = RunConfigCreate(**payload)
                    for _ in range(max(1, experiment.repetitions)):
                        configs.append((condition.label, config))

        # Validate everything before persisting anything — an experiment is one
        # intent, exactly as a batch is (ADR-0023).
        seen: set[tuple] = set()
        for _label, config in configs:
            key = (
                config.harness,
                config.executor_model,
                config.dry_run,
                config.score_mode,
                config.target_repo_id,
                config.harness_source,
            )
            if key in seen:
                continue
            seen.add(key)
            runs_service.preflight(config)

        # One ls-remote per (repository, ref) — every repetition of an arm must
        # carry the same commit, or the repetitions are not repetitions.
        pins: dict[tuple[str, str], str] = {}
        for _label, config in configs:
            pin_key = (config.target_repo_id, config.target_ref)
            if config.target_repo_id and pin_key not in pins:
                pins[pin_key] = runs_service._resolve_target(config)

        runs: list[Run] = []
        for label, config in configs:
            run = runs_service._persist_run(
                runs_service._next_run_id(config.case_id),
                config,
                target_commit=pins.get((config.target_repo_id, config.target_ref), ""),
            )
            run.experiment_id = experiment_id
            run.condition_label = label
            self.session.add(run)
            runs.append(run)
        experiment.status = ExperimentStatus.running
        experiment.updated_at = datetime.now(UTC)
        self.session.add(experiment)
        self.session.commit()

        for run in runs:
            runs_service._publish(run.run_id)
        return runs
