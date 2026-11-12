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

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.models import (
    Case,
    ConditionRole,
    Experiment,
    ExperimentCondition,
    ExperimentMode,
    ExperimentStatus,
    HarnessSource,
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
        "skills_invoke",
        "prompt_variant",
        "max_steps",
    }
)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def _variant_refusal(gaps: list[str]) -> str:
    """The refusal creation and launch owe alike: named cases, named arms.

    Both call sites answer the same question — would this run be handed a
    prompt the case never defined — so they say it with the same words.
    """
    return (
        "The design asks for prompt variants the cases do not define, so "
        "those runs would carry an empty prompt: "
        + "; ".join(sorted(set(gaps)))
        + ". Add the variant to the case, or pick variants every selected "
        "case declares."
    )


class ExperimentError(ValueError):
    """An experiment could not be created or launched as asked."""


class ExperimentNotFoundError(ExperimentError):
    """No experiment with that id."""


class ExperimentDesignError(ExperimentError):
    """The design contradicts the mode it declares."""


#: `harness_source` of an arm that sees no governance at all (ADR-0049).
BARE = "bare"

#: Prompt variants whose body directs the agent through the lab's harness. A
#: bare arm carrying one is being told to follow what it cannot read.
HARNESS_FAMILY_VARIANTS = frozenset({"harness", "swe_harness", "sdlc_full"})

#: Axes a bare arm cannot meaningfully vary — see the subsumption rule in
#: :func:`validate_design`.
_BARE_SUBSUMED = frozenset(
    {
        "prompt_variant",
        "skills_disabled",
        "skills_extra",
        "skills_invoke",
        "skills_under_test",
    }
)


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


def _skills_move_together(conditions: list[dict[str, Any]]) -> bool:
    """Is "stage the skill" and "ask for it" one intervention here, or two?

    Adding a skill and asking the agent to use it is the most common thing an
    engineer brings to this lab, and ADR-0041 is the reason: for a mode skill,
    staging it without asking is not a treatment at all. But the two knobs are
    separate fields, so the correct design lights up as "two harness factors
    vary at once" — a confound warning fired at the one design that is not
    confounded. A warning that cries wolf on the right answer teaches people to
    ignore warnings.

    They are one intervention when the arms can be put in a single order that
    both fields agree with: every pair of arms is comparable on *both* sets at
    once, so "more skills" always means "more skills asked for" and never the
    reverse. Arms that disagree — one adding X unasked while another asks for a
    Y it already had — are genuinely two factors and still warn.
    """
    pairs = []
    for condition in conditions:
        overrides = condition.get("overrides", {}) or {}
        pairs.append(
            (
                {str(item) for item in overrides.get("skills_extra") or []},
                {str(item) for item in overrides.get("skills_invoke") or []},
            )
        )
    for index, (extra_a, invoke_a) in enumerate(pairs):
        for extra_b, invoke_b in pairs[index + 1 :]:
            forward = extra_a <= extra_b and invoke_a <= invoke_b
            backward = extra_b <= extra_a and invoke_b <= invoke_a
            if not (forward or backward):
                return False
    return True


def validate_design(
    mode: ExperimentMode,
    conditions: list[dict[str, Any]],
    *,
    in_family: bool = False,
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

    if mode == ExperimentMode.subject_validation:
        # Returns before the exactly-one-control check below, because this mode
        # has no control by construction (ADR-0040): every arm is a peer, and
        # none is the baseline the others are measured against. What it reports
        # is a pass rate *per arm* — never a delta, never a pooled number.
        if "target_repo_id" in varying:
            raise ExperimentDesignError(
                "Mode 'subject_validation' holds the repository fixed. Varying it "
                "makes the result unreadable — 'the subject passed 8 of 9' says "
                "nothing without naming where. Use mode 'cross_repo' to vary the "
                "repository, or give every condition the same target."
            )
        if "skills_disabled" in varying:
            raise ExperimentDesignError(
                "Mode 'subject_validation' does not ablate anything: the subject "
                "under test is the tool and the skill driving it, not the harness. "
                "Conditions that vary 'skills_disabled' are a harness evaluation "
                "wearing this mode's label — use mode 'harness_evaluation'."
            )
        subject_axes = varying & {"harness", "executor_model"}
        if len(subject_axes) > 1:
            warnings.append(
                "Both the harness and the executor model vary. That cross product "
                "is the point of this mode, but each cell is its own population: "
                "read one <harness>/<model> arm at a time, never averaged."
            )
        return warnings

    if mode == ExperimentMode.harness_by_model:
        # The model varies by design here — the refusal `harness_evaluation`
        # raises for it is exactly what this mode exists to lift (ADR-0066). It
        # is only sound as a full grid with one harness factor: an incomplete
        # grid or two harness factors cannot separate the interaction from a
        # confound. The control role is not used; effects are read per model
        # against the first harness level.
        from harness_lab_app.factorial import grid_problems

        problems = grid_problems(conditions)
        if problems:
            raise ExperimentDesignError(
                "Mode 'harness_by_model' needs a full grid of harness levels x "
                "models with exactly one harness factor. " + " ".join(problems)
            )
        return warnings

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
            # Being asked to use a skill is a harness lever too: the same
            # skill, on the menu versus called for, is the "does it help when
            # asked" comparison (ADR-0041).
            "skills_invoke",
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
        if {"skills_extra", "skills_invoke"} <= harness_axes and _skills_move_together(
            conditions
        ):
            # One intervention wearing two field names (see above).
            harness_axes = (harness_axes - {"skills_extra", "skills_invoke"}) | {
                "skills_under_test"
            }
        # A bare arm is one axis, not four (ADR-0049). It has no prompt family
        # to hold fixed and no catalog to hold fixed: the runner masks every
        # governance directory, so whatever `prompt_variant` and `skills_*` say
        # about it are declarations that keep the condition hash honest, not
        # levers that moved. Subsume them — but only the ones that do not also
        # vary among the arms that *do* have a harness, because those are
        # genuine confounds and must still be reported.
        bare_arms = [
            condition
            for condition in conditions
            if (condition.get("overrides") or {}).get("harness_source") == BARE
        ]
        if bare_arms and "harness_source" in harness_axes:
            rest = [condition for condition in conditions if condition not in bare_arms]
            among_rest = _varying_fields(rest) if len(rest) > 1 else set()
            harness_axes -= _BARE_SUBSUMED - among_rest
        for condition in bare_arms:
            variant = str(
                (condition.get("overrides") or {}).get("prompt_variant") or ""
            )
            if variant in HARNESS_FAMILY_VARIANTS or variant.startswith("sdlc"):
                warnings.append(
                    f"Arm '{condition.get('label', '?')}' is bare but runs the "
                    f"'{variant}' prompt, which tells the agent to follow a "
                    "harness it cannot see. Give the bare arm the plain variant."
                )
        if len(harness_axes) > 1:
            warnings.append(
                "More than one harness factor varies at once "
                f"({', '.join(sorted(harness_axes))}). A difference cannot be "
                "attributed to either — vary one factor per experiment."
            )
        # A skills ablation inside the lab is a *partial* ablation, always. The
        # agent runs against this repository's own root, so CLAUDE.md's body,
        # the SessionStart hook, the subagents and .github/ governance survive
        # in both arms whatever the skill catalog says. This is a property of
        # the venue, not of which prompts the arms happen to carry, which is why
        # it is stated here rather than only when the prompts match.
        # `skills_invoke` alone moves no directory — both arms see the same
        # catalog and one is asked to open it — so "only the skill directories
        # differ" would be false for an activation experiment. A rung of a
        # ladder (ADR-0051) is exempt as well: it is single-factor *by design*,
        # read beside the rung that measured the governance body, so the
        # objection that it might be mistaken for a harness comparison does
        # not apply.
        skill_axes = harness_axes & {
            "skills_disabled",
            "skills_extra",
            "skills_under_test",
        }
        if (
            skill_axes
            and not in_family
            and all(
                (condition.get("overrides") or {}).get("harness_source", "lab_root")
                == "lab_root"
                for condition in conditions
            )
        ):
            warnings.append(
                "Every arm runs against the lab's own root, so only the skill "
                "directories differ: CLAUDE.md's body, the SessionStart hook, "
                "the subagents and .github/ governance are present in both arms. "
                "This measures the skills, not the harness."
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


def _factorial_block(
    conditions: list[ExperimentCondition],
    outcomes: dict[str, dict[str, list[bool]]],
    scored_costs: dict[str, dict[str, list[float | None]]],
) -> dict[str, Any] | None:
    """The harness x model analysis of a summarized grid (ADR-0066).

    Harness levels are ordered as the conditions were declared, so the first
    level an author lists is the baseline every effect is read against; models
    likewise, so the interaction runs from the first model listed to the last.
    """
    from harness_lab_app.factorial import analyze, harness_axis, level_of

    raw = [{"label": c.label, "overrides": dict(c.overrides or {})} for c in conditions]
    axis = harness_axis(raw)
    if axis is None:
        return None
    levels: list[str] = []
    models: list[str] = []
    grid: dict[tuple[str, str], dict[str, list[bool]]] = {}
    costs: dict[tuple[str, str], dict[str, list[float | None]]] = {}
    # Declaration order, not read order: `conditions()` sorts the control first
    # and the rest by label, which would make whichever model carries the
    # control the reference of the interaction and can flip its sign.
    declared = sorted(conditions, key=lambda row: row.id or 0)
    for condition in declared:
        overrides = condition.overrides or {}
        level = level_of({"label": condition.label, "overrides": dict(overrides)}, raw)
        model = str(overrides.get("executor_model"))
        if level not in levels:
            levels.append(level)
        if model not in models:
            models.append(model)
        grid[(level, model)] = outcomes.get(condition.label, {})
        costs[(level, model)] = scored_costs.get(condition.label, {})
    return {
        "axis": axis,
        **analyze(grid, harness_levels=levels, models=models, costs=costs),
    }


TIER_TAG_PREFIX = "tier:"


def _capability_block(
    session: Session,
    case_ids: list[str],
    conditions: list[ExperimentCondition],
    outcomes: dict[str, dict[str, list[bool]]],
) -> dict[str, Any] | None:
    """Each arm's capability frontier over cases tagged `tier:<name>` (ADR-0069).

    Tiers are ordered by name (`tier:1` < `tier:2`, `tier:T1` < `tier:T2`), so a
    corpus states its difficulty ladder in its own tags. Untagged cases are left
    out; with fewer than two tiers there is no ladder and no block.
    """
    from harness_lab_app.capability import capability_frontier

    tier_of: dict[str, str] = {}
    for case_id in case_ids:
        case = session.exec(select(Case).where(Case.case_id == case_id)).first()
        for tag in (case.tags if case else []) or []:
            if str(tag).startswith(TIER_TAG_PREFIX):
                tier_of[case_id] = str(tag)[len(TIER_TAG_PREFIX) :]
                break
    order = sorted(set(tier_of.values()))
    if len(order) < 2:
        return None
    arms = []
    for condition in sorted(conditions, key=lambda row: row.id or 0):
        by_tier: dict[str, dict[str, list[bool]]] = {tier: {} for tier in order}
        for case_id, runs in outcomes.get(condition.label, {}).items():
            if case_id in tier_of:
                by_tier[tier_of[case_id]][case_id] = runs
        reading = capability_frontier(by_tier, order=order)
        arms.append({"label": condition.label, **reading})
    return {"tiers": order, "arms": arms}


# A pruning family (ADR-0076): one control with the full harness and one
# treatment per item, each removing exactly that item. `scripts/prune.py`
# names the family with this prefix; the summary reads it as leave-one-out.
PRUNING_FAMILY_PREFIX = "prune-"


def removed_item(control: dict[str, Any], treatment: dict[str, Any]) -> str | None:
    """The single item a treatment removes relative to its control, or None.

    Today an item is a skill (``skills_disabled``). Anything else — two skills
    at once, a different prompt — is not a leave-one-out arm and gets no
    pruning verdict, rather than a verdict about the wrong thing.
    """
    if {k: v for k, v in control.items() if k != "skills_disabled"} != {
        k: v for k, v in treatment.items() if k != "skills_disabled"
    }:
        return None
    removed = set(treatment.get("skills_disabled") or []) - set(
        control.get("skills_disabled") or []
    )
    added = set(control.get("skills_disabled") or []) - set(
        treatment.get("skills_disabled") or []
    )
    if len(removed) != 1 or added:
        return None
    return f"skill:{removed.pop()}"


def _pruning_block(
    conditions: list[ExperimentCondition],
    control_label: str,
    outcomes: dict[str, dict[str, list[bool]]],
    case_metrics: dict[str, dict[str, dict[str, list[float]]]],
) -> list[dict[str, Any]]:
    """One removal verdict per leave-one-out treatment arm, keep-first."""
    from harness_lab_app.pruning import assess_removal

    control = next(c for c in conditions if c.label == control_label)
    verdicts: list[dict[str, Any]] = []
    for condition in conditions:
        if condition.label == control_label:
            continue
        item = removed_item(control.overrides or {}, condition.overrides or {})
        if item is None:
            continue
        verdict = assess_removal(
            item=item,
            control_success=outcomes.get(control_label, {}),
            treatment_success=outcomes.get(condition.label, {}),
            control_metrics=case_metrics.get(control_label, {}),
            treatment_metrics=case_metrics.get(condition.label, {}),
        )
        verdicts.append({"arm": condition.label, **verdict.as_dict()})
    order = {"keep": 0, "inconclusive": 1, "removable": 2}
    return sorted(verdicts, key=lambda v: (order.get(v["decision"], 9), v["item"]))


def repetition_warning(repetitions: int) -> str | None:
    """State plainly what a given N does and does not support."""
    if repetitions < 2:
        return (
            "1 repetition — exploratory only. Agents are stochastic; a single "
            "trial per arm cannot separate a real difference from run-to-run "
            "variance. Use 2 for a judgement."
        )
    if repetitions < 5:
        return (
            f"{repetitions} repetitions — a judgement, not a validation. Use 5 "
            "or more before treating a delta as settled."
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
            assess_validity,
            compare,
            objective_signature,
            paired_by_case,
            pass_k_by_arm,
            plan_repetitions,
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
        # Per arm, per case: every scored outcome. Reduced to one verdict per
        # cell for the paired case matrix *after* the loop — the cell used to
        # keep whichever repetition came last, so a 1-of-5 pass and a 5-of-5
        # pass rendered identically.
        outcomes: dict[str, dict[str, list[bool]]] = {
            condition.label: {} for condition in conditions
        }
        # Per arm, per case: how many runs were scored and how many passed.
        case_counts: dict[str, dict[str, dict[str, int]]] = {
            condition.label: {} for condition in conditions
        }
        # Per arm, per case: the cost of every *scored* run, None where a scored
        # run reported no cost. Read by the harness x model block (ADR-0066),
        # which must price exactly the runs it counts, never a subtotal.
        scored_costs: dict[str, dict[str, list[float | None]]] = {
            condition.label: {} for condition in conditions
        }
        # Why the arms stopped, per arm. Two arms that terminate under
        # different rules are not two measurements of the same quantity, and
        # the verdict has to say so before anyone reads the effort delta.
        stop_reasons: dict[str, list[str]] = {
            condition.label: [] for condition in conditions
        }
        # Runs that measured the connection rather than the subject. A
        # `harness_error` archived at exit 0 used to enter the corpus as a model
        # scoring zero; counting it here is what lets the reader see it as
        # infrastructure without rewriting the run's recorded state.
        # `unmeasured` is a different accident from the others: the run finished,
        # and its *checks* settled nothing (a missing binary, a sandbox with no
        # network). Nothing else counts those — an arm with no crash is skipped
        # by the health rule and an arm with no score has no rate to floor.
        run_health: dict[str, dict[str, int]] = {
            condition.label: {
                "runs": 0,
                "harness_error": 0,
                "failed": 0,
                "unhealthy": 0,
                "scored_runs": 0,
                "unmeasured": 0,
            }
            for condition in conditions
        }
        # What each run's checks did, comparably across runs: two arms failing
        # the same command in every repetition is the environment deciding, and
        # the floor issue needs to be able to say so (ADR-0062).
        check_signatures: dict[str, list[Any]] = {
            condition.label: [] for condition in conditions
        }
        # Whether the skills an arm is about were actually opened (ADR-0041).
        # A comparison whose treatment arm never invoked its skill measured
        # availability, and the verdict has to say so before the delta.
        skill_usage: dict[str, dict[str, Any]] = {
            condition.label: {"skills": [], "runs": 0, "invoked": 0, "requested": False}
            for condition in conditions
        }
        # What each arm is *about* follows the same rule the scorer used when it
        # wrote `skill_invoked` (``score_service.build_context``): the skills an
        # arm stages or is asked for, plus the ones a sibling arm removes. The
        # second half is what makes a full-harness treatment legible — it stages
        # nothing and asks for nothing, and the whole catalog is only named on
        # the control's `skills_disabled`. Reading the arm's own overrides alone
        # left that arm with an empty skill list, so the observation that none
        # of its twenty-five skills was ever opened had nowhere to land.
        # Per arm, per metric, per case: the values the pruning verdict reads
        # (ADR-0076). Only metrics whose direction is fixed in PRUNING_METRICS.
        from harness_lab_app.pruning import PRUNING_METRICS

        pruning_metrics = tuple(PRUNING_METRICS)
        case_metrics: dict[str, dict[str, dict[str, list[float]]]] = {
            condition.label: {} for condition in conditions
        }
        removed_elsewhere: dict[str, list[str]] = {}
        for condition in conditions:
            mine = set((condition.overrides or {}).get("skills_disabled") or [])
            others: set[str] = set()
            for sibling in conditions:
                if sibling.label == condition.label:
                    continue
                others.update((sibling.overrides or {}).get("skills_disabled") or [])
            removed_elsewhere[condition.label] = sorted(others - mine)
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
            config = run_service.get_run_config(run.run_config_id)
            case_id = config.case_id
            crashed = run.state in (RunState.failed, RunState.cancelled)
            invoked = values.get("skill_invoked")
            # A skill left unopened by a run that died at minute two is not
            # evidence about activation.
            if invoked is not None and not crashed:
                usage = skill_usage[label]
                usage["runs"] += 1
                usage["invoked"] += int(float(invoked) > 0)
                usage["requested"] = usage["requested"] or bool(config.skills_invoke)
                # A bare arm sees no catalog whatever its overrides say — the
                # runner masks every skill directory (ADR-0049) — so it is about
                # no skill, and `treatment_not_activated` must never fire on it.
                names = (
                    set()
                    if config.harness_source == HarnessSource.bare
                    else (set(config.skills_extra) | set(config.skills_invoke))
                    # Minus what this run itself cannot see, mirroring the
                    # scorer: an arm is not measured against a hidden skill.
                    | (
                        set(removed_elsewhere.get(label, []))
                        - set(config.skills_disabled)
                    )
                )
                for name in sorted(names):
                    if name not in usage["skills"]:
                        usage["skills"].append(name)
            for metric in pruning_metrics:
                metric_value = values.get(metric)
                if isinstance(metric_value, (int, float)) and not isinstance(
                    metric_value, bool
                ):
                    case_metrics[label].setdefault(metric, {}).setdefault(
                        case_id, []
                    ).append(float(metric_value))
            passed = values.get("task_validation_pass")
            if passed is not None:
                outcomes[label].setdefault(case_id, []).append(bool(passed))
                cost_value = values.get("cost_usd")
                scored_costs[label].setdefault(case_id, []).append(
                    float(cost_value) if isinstance(cost_value, (int, float)) else None
                )
            cell = case_counts[label].setdefault(
                case_id, {"runs": 0, "scored_runs": 0, "successes": 0}
            )
            cell["runs"] += 1
            if passed is not None:
                cell["scored_runs"] += 1
                cell["successes"] += int(bool(passed))
            validation = self.session.exec(
                select(Validation).where(Validation.run_id == run.run_id)
            ).first()
            reason = (
                (validation.effort or {}).get("stop_reason") if validation else None
            )
            if reason:
                stop_reasons[label].append(str(reason))
            objective = (validation.objective if validation else None) or {}
            check_signatures[label].append(objective_signature(objective, run.run_id))
            health = run_health[label]
            health["runs"] += 1
            health["scored_runs"] += int(values.get("task_validation_pass") is not None)
            health["harness_error"] += int(reason == "harness_error")
            health["failed"] += int(crashed)
            health["unhealthy"] += int(crashed or reason == "harness_error")
            # A manual-only case declares no checks and was never meant to be
            # measured; only a run that *had* checks and got no verdict counts.
            health["unmeasured"] += int(
                bool(objective.get("checks")) and objective.get("passed") is None
            )

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
        # One verdict per cell: a case counts as passed for an arm only when
        # every scored repetition passed. Strict on purpose — "fixed" should
        # mean the treatment made the case reliably pass, not once.
        verdicts: dict[str, dict[str, bool | None]] = {
            label: {case_id: all(runs) for case_id, runs in cells.items()}
            for label, cells in outcomes.items()
        }
        comparisons: list[dict[str, Any]] = []
        # Discovery has no control arm by construction (ADR-0028), and neither
        # does subject validation (ADR-0040). Neither must acquire one here: a
        # mode that cannot support a causal claim never renders a
        # control-versus-treatment delta, whatever roles the rows happen to
        # carry.
        # A harness x model grid (ADR-0066) has no single pair to compare: any
        # control-versus-treatment delta across two cells would mix the model
        # with the harness. It is read through its `factorial` block instead.
        if experiment.mode in (
            ExperimentMode.discovery,
            ExperimentMode.subject_validation,
            ExperimentMode.harness_by_model,
        ):
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
                        # Every repetition per case, so the delta's interval
                        # is resampled by case rather than by run (ADR-0064).
                        "success": success_delta(
                            summaries[control.label],
                            summaries[condition.label],
                            control_cases=outcomes[control.label],
                            treatment_cases=outcomes[condition.label],
                        ),
                        "case_matrix": paired_by_case(
                            verdicts[control.label], verdicts[condition.label]
                        ),
                    }
                )

        first: dict[str, Any] | None = comparisons[0] if comparisons else None
        # Before the verdict, not beside it: whether this comparison could have
        # measured the thing it names (ADR-0046).
        validity = assess_validity(
            mode=str(experiment.mode.value),
            control=summaries[first["control"]] if first else None,
            treatment=summaries[first["treatment"]] if first else None,
            # Every arm, not the compared pair: a floor is a property of the
            # whole design, and a mode without a control still has one.
            arms=list(summaries.values()),
            skill_usage=skill_usage,
            run_health=run_health,
            check_signatures=check_signatures,
            success=first["success"] if first else None,
            design_notes=list(experiment.design_notes or []),
        )
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
            # Subject validation reads every arm rather than a control/treatment
            # pair, so it needs the arms themselves — there is no `first`.
            subject=experiment.subject,
            arms=list(summaries.values()),
            skill_usage=skill_usage,
            validity=validity,
        )
        # Reliability across repetitions, every arm at one shared k, and the
        # repetition count a 20-point move would need. Both are ADR-0064: the
        # first says how often a case passes *every* time, the second is advice
        # for the next design — offered only where arms are compared at all.
        pass_k = pass_k_by_arm(outcomes)
        plan = (
            plan_repetitions(list(summaries.values()), cases=len(experiment.case_ids))
            if control is not None
            else None
        )

        return {
            "experiment_id": experiment_id,
            "question": experiment.question,
            "mode": experiment.mode,
            "subject": experiment.subject,
            "repetitions": experiment.repetitions,
            "case_repetitions": dict(experiment.case_repetitions),
            "suite_id": experiment.suite_id,
            "family_id": experiment.family_id,
            "verdict": answer,
            "validity": validity,
            "run_health": run_health,
            "conditions": [
                {
                    "label": summary.label,
                    "role": next(
                        c.role for c in conditions if c.label == summary.label
                    ),
                    "runs": summary.runs,
                    "scored_runs": summary.scored_runs,
                    "success_rate": summary.success_rate,
                    # The rate's 95% Wilson interval; None with nothing scored.
                    "success_rate_ci_low": (
                        summary.success_rate_ci[0] if summary.success_rate_ci else None
                    ),
                    "success_rate_ci_high": (
                        summary.success_rate_ci[1] if summary.success_rate_ci else None
                    ),
                    "pass_k": pass_k.get(summary.label),
                    "warnings": summary.warnings,
                    "metrics": {
                        name: aggregate.__dict__
                        for name, aggregate in summary.metrics.items()
                    },
                }
                for summary in summaries.values()
            ],
            "comparisons": comparisons,
            "repetition_plan": plan,
            # The one planning figure, flat, for a reader that wants only it:
            # repetitions per case per arm to detect a 20-point difference.
            "repetitions_needed_for_20pp": (
                plan["repetitions_per_case"] if plan else None
            ),
            # Per-arm, per-case counts: {label: {case_id: {runs, scored_runs,
            # successes}}}. Not a comparison — `comparisons[].case_matrix` pairs
            # a control against a treatment, which a mode without a control
            # never has. This is the raw grid, with the repetitions kept, which
            # is the only matrix subject validation can show and is useful under
            # every mode.
            "case_outcomes": case_counts,
            # The harness x model reading (ADR-0066); None under every other mode.
            "factorial": (
                _factorial_block(conditions, outcomes, scored_costs)
                if experiment.mode == ExperimentMode.harness_by_model
                else None
            ),
            # Per arm, the highest difficulty tier it does *reliably*, when the
            # cases are tagged `tier:<name>` (ADR-0069). None otherwise.
            "capability": _capability_block(
                self.session, experiment.case_ids, conditions, outcomes
            ),
            # Leave-one-out: can each ablated item go? (ADR-0076). Only for a
            # pruning family, whose every treatment removes exactly one item.
            "pruning": (
                _pruning_block(conditions, control.label, outcomes, case_metrics)
                if control is not None
                and str(experiment.family_id or "").startswith(PRUNING_FAMILY_PREFIX)
                else None
            ),
        }

    def try_complete(self, experiment_id: str) -> Experiment | None:
        """Mark a running experiment complete once every run has stopped.

        `ExperimentStatus.complete` existed in the enum and was assigned by no
        line of this codebase, so an experiment stayed `running` forever — long
        after its last run had finished, its audit had been read and its
        conclusions written. "Running" then meant nothing, and the attention
        feed could not tell work in flight from work nobody had closed.

        Returns the experiment when this call is what completed it, and None
        otherwise — so a caller can act exactly once (queue the reading) rather
        than on every run that finishes.
        """
        from harness_lab_app.experiment_audit_service import runs_finished

        try:
            experiment = self.get_experiment(experiment_id)
        except ExperimentNotFoundError:
            return None
        if experiment.status != ExperimentStatus.running:
            return None  # draft, abandoned, or already complete
        finished, _done, _total = runs_finished(self.session, experiment_id)
        if not finished:
            return None
        experiment.status = ExperimentStatus.complete
        experiment.updated_at = datetime.now(UTC)
        self.session.add(experiment)
        self.session.commit()
        self.session.refresh(experiment)
        return experiment

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

    def _check_subject_validation(self, data: ExperimentCreate) -> None:
        """Refuse a subject validation that cannot produce its own number.

        Two requirements, both hard, both for the same reason: this mode's whole
        output is "the subject passed N of M under <arm>", and neither half of
        that sentence can be reconstructed later.

        The validation command is a rejection rather than a warning because of
        how the pipeline fails without one. ``build_validation_spec`` silently
        drops entries that do not look executable, so a case whose only
        "validation" is prose yields zero commands, ``parse_objective`` returns
        ``passed=None``, ``task_validation_pass`` is never emitted, and the arm
        ends with ``scored_runs == 0`` and no pass rate at all. The experiment
        runs in full, costs real money, and answers nothing — without ever
        saying so. Better to refuse at creation, while it is still one edit away.
        """
        from harness_lab_app.validation import _looks_executable

        if not data.subject.strip():
            raise ExperimentError(
                "Mode 'subject_validation' needs a subject: the tool, service or "
                "skill under test. The result this mode produces is a sentence "
                "about that subject, and it cannot be recovered from the arms."
            )
        if not data.case_ids:
            raise ExperimentError(
                "Mode 'subject_validation' needs at least one case. The case is "
                "the definition of what 'works' means for this subject."
            )
        unvalidated: list[str] = []
        for case_id in data.case_ids:
            case = self.session.exec(
                select(Case).where(Case.case_id == case_id)
            ).first()
            if case is None:
                raise ExperimentError(f"Unknown case: {case_id}")
            if not any(_looks_executable(raw) for raw in case.validation_commands):
                unvalidated.append(case_id)
        if unvalidated:
            raise ExperimentError(
                "Mode 'subject_validation' measures a pass rate, so every case "
                "must declare at least one executable validation command. These "
                f"do not: {', '.join(unvalidated)}. Without one the runs execute, "
                "produce no verdict, and the experiment reports nothing — add a "
                "command that exits non-zero when the subject fails."
            )

    def _refuse_tautological_cases(self, case_ids: list[str]) -> None:
        """Refuse to launch a case whose checks already passed on the clean tree.

        A dry run (``POST /api/cases/{id}/dry-run``) executes the case's own
        checks against the pinned commit with no agent work applied. When they
        all pass there, the case cannot tell work from no work — every run it
        produces will report a green that means nothing, at full cost. Two of
        the talos cases were exactly that, and four experiment arms were spent
        on them before anybody read the stored detail.

        Silent for a case that was never dry-run: this refuses a *known* defect
        rather than demanding a ceremony.
        """
        tautological: list[str] = []
        for case_id in case_ids:
            case = self.session.exec(
                select(Case).where(Case.case_id == case_id)
            ).first()
            if case is None:
                continue
            raw = (case.metadata_json or {}).get("dry_run") or ""
            try:
                record = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                continue
            if isinstance(record, dict) and record.get("verdict") == "tautological":
                tautological.append(case_id)
        if tautological:
            raise ExperimentError(
                "These cases passed their own checks on the clean tree, before "
                f"any agent touched it: {', '.join(tautological)}. Their green "
                "measures the case, not the agent — re-author the checks so at "
                "least one fails on the untouched repository, then dry-run again."
            )

    def _check_declared_variants(self, data: ExperimentCreate) -> None:
        """Refuse at creation a design whose cases do not define its prompts.

        ``launch`` already refuses this, but it refuses *after* the experiment
        is persisted: the design that cannot run leaves an experiment behind,
        and the next attempt at the same question has to be filed under a
        suffixed id. Which variant each arm fixes is knowable here, so the
        refusal belongs here too — nothing is created that cannot launch
        (ADR-0059).

        Only fully-determined pairs are checked. When neither the condition nor
        ``prompt_variants`` names a variant, the runs carry whatever the launch
        payload supplies, which does not exist yet; ``launch`` keeps the check
        for those. An unknown case is left to ``launch`` as well — a draft may
        legitimately name a case that is not imported yet.
        """
        case_ids = [case_id for case_id in data.case_ids if case_id]
        if not case_ids or not data.conditions:
            return
        defined = RunService(self.session)._defined_variants(case_ids)
        gaps: list[str] = []
        for case_id in case_ids:
            available = defined.get(case_id)
            if available is None:
                continue
            for condition in data.conditions:
                # A condition's override wins over the experiment-level axis,
                # exactly as it does when ``launch`` builds the run config.
                fixed = (condition.overrides or {}).get("prompt_variant")
                wanted = [fixed] if fixed else list(data.prompt_variants)
                gaps.extend(
                    f"case '{case_id}' / arm '{condition.label}' asks for "
                    f"prompt variant '{variant}'"
                    for variant in wanted
                    if variant and variant not in available
                )
        if gaps:
            raise ExperimentError(_variant_refusal(gaps))

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
        if data.mode == ExperimentMode.subject_validation:
            self._check_subject_validation(data)
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
        self._check_declared_variants(data)
        # Objections about the *design*: they stay true for the life of the
        # experiment, so they are persisted and read back beside the verdict.
        # The warnings below them are about this moment (no cases yet, too few
        # repetitions) and are answered by the verdict's own caveats later.
        design_notes = validate_design(
            data.mode, conditions, in_family=bool(data.family_id.strip())
        )
        design_notes.extend(
            note.strip() for note in data.design_notes if note and note.strip()
        )
        warnings = list(design_notes)
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
            subject=data.subject.strip(),
            case_ids=list(data.case_ids),
            prompt_variants=list(data.prompt_variants),
            repetitions=data.repetitions,
            case_repetitions={
                case_id: count
                for case_id, count in data.case_repetitions.items()
                if case_id in data.case_ids
            },
            suite_id=data.suite_id,
            family_id=data.family_id.strip(),
            design_notes=design_notes,
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
        "skills_invoke",
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
                # The skill axes are part of what ran: a relaunch that dropped
                # them would be refused by the lock, and a subject validation
                # carries its skill in the shared config, not in an arm.
                "skills_disabled": list(config.skills_disabled),
                "skills_extra": list(config.skills_extra),
                "skills_invoke": list(config.skills_invoke),
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
        # Which prompts each case actually declares. A design may name a variant
        # a case does not define — the shared axis crosses every case with every
        # variant, and a condition may fix a ``prompt_variant`` of its own — and
        # the run would then be handed an empty prompt, silently: the prompt
        # lookup falls back to "" for an unknown variant. That failure lands
        # inside one arm and reads as that arm losing. Batch mode resolves this
        # per case before persisting anything (ADR-0023); an experiment owes the
        # same guarantee.
        defined = runs_service._defined_variants(experiment.case_ids)
        undefined: list[str] = []

        self._refuse_tautological_cases(experiment.case_ids)

        configs: list[tuple[str, RunConfigCreate]] = []
        for case_id in experiment.case_ids:
            available = defined.get(case_id)
            if available is None:
                raise ExperimentError(
                    f"Unknown case '{case_id}'. Use GET /api/cases for the valid ids."
                )
            for variant in experiment.prompt_variants or [base.prompt_variant]:
                for condition in conditions:
                    payload = base.model_dump()
                    payload.update(
                        {"case_id": case_id, "prompt_variant": variant, "resume": False}
                    )
                    payload.update(condition.overrides)
                    config = RunConfigCreate(**payload)
                    if config.prompt_variant not in available:
                        undefined.append(
                            f"case '{case_id}' / arm '{condition.label}' asks for "
                            f"prompt variant '{config.prompt_variant}'"
                        )
                        continue
                    for _ in range(experiment.repetitions_for(case_id)):
                        configs.append((condition.label, config))

        if undefined:
            raise ExperimentError(_variant_refusal(undefined))
        if not configs:
            raise ExperimentError(f"Experiment '{experiment_id}' expands to no runs.")

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
                tuple(sorted(config.skills_invoke)),
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
