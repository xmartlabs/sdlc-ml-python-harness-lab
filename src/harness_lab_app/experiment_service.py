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
    RunConfig,
    RunState,
    Validation,
)
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate

if TYPE_CHECKING:
    from harness_lab_app.schemas import ExperimentCreate

# Axis fields a condition may fix. Anything else is either shared by the whole
# experiment (cases, repetitions) or not an axis at all.
#
# ``prompt_variant`` is an axis on purpose: the governance-loaded variant of a
# prompt (``sdlc``, ``swe_harness``) IS part of the harness, and the lab's
# original A/B — bare prompt versus harness-wrapped — is expressed as exactly
# this axis. An experiment-level ``prompt_variants`` list still exists for the
# other use: running every arm across several variants as strata.
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
        "prompt_variant",
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
            # The governance-loaded prompt is a harness lever like any skill:
            # naive-vs-sdlc and swe_default-vs-swe_harness are the lab's
            # founding comparisons.
            "prompt_variant",
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
    def __init__(self, session: Session, enqueue=None, enqueue_cleanup=None):
        self.session = session
        # Queue publisher, injected by the API layer exactly as RunService gets
        # it. Without this, `launch` persisted its runs and then silently never
        # enqueued them — RunService._publish() no-ops when the callback is
        # absent — leaving a whole experiment `pending` forever with no error
        # anywhere. Only `launch` needs it; every read path passes nothing.
        self.enqueue = enqueue
        self.enqueue_cleanup = enqueue_cleanup

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
            verdict,
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
        # Why the arms stopped, per arm. Two arms that terminate under
        # different rules are not two measurements of the same quantity, and
        # the verdict has to say so before anyone reads the effort delta.
        stop_reasons: dict[str, list[str]] = {
            condition.label: [] for condition in conditions
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
            validation = self.session.exec(
                select(Validation).where(Validation.run_id == run.run_id)
            ).first()
            reason = (
                (validation.effort or {}).get("stop_reason") if validation else None
            )
            if reason:
                stop_reasons[label].append(str(reason))

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
        comparisons: list[dict[str, Any]] = []
        # Discovery has no control arm by construction (ADR-0028) and must not
        # acquire one here: a mode that cannot support a causal claim never
        # renders a control-versus-treatment delta, whatever roles the rows
        # happen to carry.
        if experiment.mode == ExperimentMode.discovery:
            control = None
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

        first: dict[str, Any] | None = comparisons[0] if comparisons else None
        answer = verdict(
            mode=str(experiment.mode.value),
            cases=len(experiment.case_ids),
            repetitions=min(
                [experiment.repetitions, *experiment.case_repetitions.values()]
            ),
            control=summaries[first["control"]] if first else None,
            treatment=summaries[first["treatment"]] if first else None,
            success=first["success"] if first else None,
            case_matrix=first["case_matrix"] if first else None,
            stop_reasons=stop_reasons,
        )

        return {
            "experiment_id": experiment_id,
            "question": experiment.question,
            "mode": experiment.mode,
            "repetitions": experiment.repetitions,
            "case_repetitions": dict(experiment.case_repetitions),
            "suite_id": experiment.suite_id,
            "verdict": answer,
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

    def abandon_experiment(self, experiment_id: str) -> Experiment:
        """Take an experiment out of the working set without erasing anything.

        Abandoned experiments disappear from the default list and from the
        attention feed, but their runs STAY in the corpus — abandonment is
        "stop showing me this", not "this never happened". To remove the
        measurements themselves, delete the experiment with its runs.
        """
        experiment = self.get_experiment(experiment_id)
        experiment.status = ExperimentStatus.abandoned
        experiment.updated_at = datetime.now(UTC)
        self.session.add(experiment)
        self.session.commit()
        self.session.refresh(experiment)
        return experiment

    def delete_experiment(
        self, experiment_id: str, *, delete_runs: bool = False
    ) -> dict[str, Any]:
        """Erase an experiment — and, explicitly, its measurements.

        Deleting the experiment while leaving its runs would strand orphan
        measurements in every dashboard, which is precisely the bias a cleanup
        is trying to remove — so an experiment with runs is only deletable
        together with them, and only once nothing is still executing.
        """
        experiment = self.get_experiment(experiment_id)
        runs = self.runs(experiment_id)
        active = [
            run for run in runs if run.state in (RunState.pending, RunState.running)
        ]
        if active:
            raise ExperimentError(
                f"Experiment '{experiment_id}' has {len(active)} run(s) still "
                "pending or running. Cancel them first — deleting a live "
                "measurement mid-flight is how corpora grow unexplained holes."
            )
        if runs and not delete_runs:
            raise ExperimentError(
                f"Experiment '{experiment_id}' has {len(runs)} run(s). Deleting "
                "it without them would leave orphan measurements biasing every "
                "aggregate — pass delete_runs=true to remove them too, or "
                "abandon the experiment to hide it while keeping the corpus."
            )
        runs_service = RunService(self.session, enqueue_cleanup=self.enqueue_cleanup)
        for run in runs:
            # force: continuation chains inside the experiment go with it.
            runs_service.delete_run(run.run_id, force=True)
        for condition in self.conditions(experiment_id):
            self.session.delete(condition)
        self.session.delete(experiment)
        self.session.commit()
        return {"experiment_id": experiment_id, "deleted_runs": len(runs)}

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
        # The weakest case governs the caveat: an experiment whose SWE-bench
        # instance runs once is exploratory *for that case* however many times
        # the canaries repeat.
        note = repetition_warning(
            min([data.repetitions, *data.case_repetitions.values()])
        )
        if note:
            warnings.append(note)
        if not data.case_ids:
            # Creating is allowed — a draft can exist before its cases do — but
            # this is the single most confusing failure to hit later ("has no
            # cases", at launch, three screens away), so it is named now.
            warnings.append(
                "This experiment has no cases, so it cannot launch. Set case_ids "
                "before launching — an experiment measures nothing without them."
            )

        experiment = Experiment(
            experiment_id=experiment_id,
            name=data.name or experiment_id,
            question=data.question.strip(),
            mode=data.mode,
            case_ids=list(data.case_ids),
            prompt_variants=list(data.prompt_variants),
            repetitions=data.repetitions,
            case_repetitions={
                case_id: count
                for case_id, count in data.case_repetitions.items()
                if case_id in data.case_ids
            },
            suite_id=data.suite_id,
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

    #: What a launch locks for the life of the experiment. Everything here is
    #: part of what was measured; skills are included because an arm's ablation
    #: is its identity, not a setting.
    _LOCKED_FIELDS = (
        "harness",
        "executor_model",
        "planner_model",
        "prompt_variant",
        "max_steps",
        "skills_disabled",
        "skills_extra",
    )

    @staticmethod
    def _locked_value(value: Any) -> Any:
        return sorted(value) if isinstance(value, list) else value

    def _check_launch_consistency(
        self, experiment_id: str, configs: list[tuple[str, RunConfigCreate]]
    ) -> None:
        """Refuse a relaunch that would mix two configurations in one name."""
        runs_service = RunService(self.session)
        reference: dict[tuple[str, str], RunConfig] = {}
        for run in self.runs(experiment_id):
            recorded = runs_service.get_run_config(run.run_config_id)
            if recorded.dry_run:
                continue
            reference.setdefault(
                (recorded.case_id, run.condition_label or ""), recorded
            )
        if not reference:
            return
        for label, config in configs:
            prior = reference.get((config.case_id, label))
            if prior is None:
                continue
            diffs = [
                f"{field}: {self._locked_value(getattr(prior, field))!r} → "
                f"{self._locked_value(getattr(config, field))!r}"
                for field in self._LOCKED_FIELDS
                if self._locked_value(getattr(prior, field))
                != self._locked_value(getattr(config, field))
            ]
            if diffs:
                raise ExperimentError(
                    f"Experiment '{experiment_id}' was already launched with a "
                    f"different configuration for arm '{label}' — "
                    + "; ".join(diffs)
                    + ". Mixing configurations inside one experiment files two "
                    "measurements under one name. Relaunch with the same "
                    "configuration to add repetitions, or create a new experiment."
                )

    def launch_config(self, experiment_id: str) -> dict[str, Any] | None:
        """The shared configuration this experiment's real runs carry.

        Read from the first non-dry run rather than stored separately, so it
        cannot drift from what actually executed. ``None`` while nothing real
        has run — a draft is still free to choose.
        """
        runs_service = RunService(self.session)
        for run in self.runs(experiment_id):
            config = runs_service.get_run_config(run.run_config_id)
            if config.dry_run:
                continue
            return {
                "harness": config.harness,
                "executor_model": config.executor_model,
                "planner_model": config.planner_model,
                "prompt_variant": config.prompt_variant,
                "max_steps": config.max_steps,
                "score_mode": config.score_mode,
            }
        return None

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

        runs_service = RunService(self.session, enqueue=self.enqueue)
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
                    for _ in range(experiment.repetitions_for(case_id)):
                        configs.append((condition.label, config))

        # One experiment, one measurement configuration. A second launch with a
        # different shared setup (another model, another budget) would file two
        # different measurements under the same experiment_id, and the summary
        # would average them as though they were one. Same-config launches stay
        # allowed — that is how repetitions are added. Dry runs are rehearsals:
        # they neither lock the configuration nor are blocked by it.
        if not base.dry_run:
            self._check_launch_consistency(experiment_id, configs)

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
                # The skill axes MUST be part of this key. They are exactly what
                # a Mode A experiment varies, so without them the treatment arm
                # dedupes onto its own baseline and its config is never
                # preflighted — an unknown staged skill then fails at *run*
                # time, inside one arm, which reads as a treatment effect.
                tuple(sorted(config.skills_disabled)),
                tuple(sorted(config.skills_extra)),
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
                experiment_id=experiment_id,
                condition_label=label,
            )
            runs.append(run)
        experiment.status = ExperimentStatus.running
        experiment.updated_at = datetime.now(UTC)
        self.session.add(experiment)
        self.session.commit()

        for run in runs:
            runs_service._publish(run.run_id)
        return runs
