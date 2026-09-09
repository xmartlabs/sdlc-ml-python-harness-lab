"""Aggregate a condition's repetitions, and compare two conditions honestly.

Agents are stochastic and local models at temperature > 0 are reproducible only
*statistically*, which the lab has held as a methodological position since
[ADR-0003](../../docs/adr/0003-trial-provenance-and-harness-fingerprint.md).
This module is that position made mechanical: a single trial is reported as a
single trial, a spread is always shown next to a mean, and a delta arrives with
the reason it might mean nothing.

Deliberately not here: p-values and significance tests. Good repetitions and
visible distributions first; a t-test over four runs would dress three runs of
noise in the vocabulary of proof.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Repetition thresholds. Product defaults, not statistical law — what they buy is
# a UI that cannot silently present one run as a result.
# Three named levels, chosen for the budget a lab can actually spend: one
# repetition explores, two is enough for a judgement the page will state, five
# is a validation. The wording ladder below never says "significant".
EXPLORATORY_ONLY = 2  # below this: exploratory, no directional headline
USEFUL_COMPARISON = 2  # from this: a judgement
STRONGER_EVIDENCE = 5  # from this: a validation


@dataclass
class Aggregate:
    """One metric, over the repetitions of one condition."""

    name: str
    n: int
    mean: float | None = None
    median: float | None = None
    stdev: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    # How many repetitions produced no value at all. Kept separate from `n` so a
    # mean over 2 of 10 runs can never be read as a mean over 10.
    missing: int = 0

    @property
    def spread(self) -> str:
        if self.mean is None:
            return "—"
        if self.stdev is None:
            return f"{self.mean:.3g}"
        return f"{self.mean:.3g} ± {self.stdev:.3g}"


@dataclass
class ConditionSummary:
    label: str
    runs: int
    successes: int = 0
    scored_runs: int = 0
    metrics: dict[str, Aggregate] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float | None:
        """Share of *scored* runs that passed, or None when nothing was scored.

        Denominated in scored runs rather than queued ones: a run that never
        produced a verdict is unknown, and counting it as a failure would make
        an infrastructure problem look like a harness problem.
        """
        if not self.scored_runs:
            return None
        return self.successes / self.scored_runs


@dataclass
class Delta:
    """One metric's difference between a control and a treatment."""

    name: str
    control: float | None
    treatment: float | None
    absolute: float | None = None
    relative: float | None = None
    direction: str = "neutral"
    # Whether the difference is larger than the noise either arm shows on its
    # own. Not a significance test — a guard against reading a delta that sits
    # entirely inside the spread.
    exceeds_spread: bool | None = None

    @property
    def improved(self) -> bool | None:
        if self.absolute is None or self.direction == "neutral":
            return None
        return (
            self.absolute > 0
            if self.direction == "higher_is_better"
            else (self.absolute < 0)
        )


def aggregate_values(name: str, values: list[float], missing: int = 0) -> Aggregate:
    """Mean, median, spread and range over one metric's observed values."""
    if not values:
        return Aggregate(name=name, n=0, missing=missing)
    return Aggregate(
        name=name,
        n=len(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
        # A single observation has no spread. Reporting 0.0 would claim perfect
        # consistency from one run, which is the opposite of what it shows.
        stdev=statistics.stdev(values) if len(values) > 1 else None,
        minimum=min(values),
        maximum=max(values),
        missing=missing,
    )


def repetition_warnings(n: int) -> list[str]:
    """What a given number of repetitions does and does not support."""
    if n == 0:
        return ["No runs have completed for this condition yet."]
    if n < EXPLORATORY_ONLY:
        return [
            "Only 1 repetition — exploratory result. A single trial cannot "
            "separate a real difference from run-to-run variance; use "
            f"{USEFUL_COMPARISON} for a judgement."
        ]
    if n < STRONGER_EVIDENCE:
        return [
            f"{n} repetitions — a judgement, not a validation. {STRONGER_EVIDENCE} "
            "or more makes it one."
        ]
    return []


def summarize_condition(
    label: str,
    run_scores: list[dict[str, Any]],
    *,
    success_metric: str = "task_validation_pass",
) -> ConditionSummary:
    """Aggregate every metric across one condition's runs.

    ``run_scores`` is one mapping per run: ``{score_name: value}``. Missing keys
    are counted, not imputed — a run that produced no cost figure has unknown
    cost, and substituting zero would flatter it.
    """
    names = sorted({name for scores in run_scores for name in scores})
    summary = ConditionSummary(label=label, runs=len(run_scores))

    for name in names:
        if name == success_metric:
            continue
        values = [
            float(scores[name])
            for scores in run_scores
            if isinstance(scores.get(name), (int, float))
            and not isinstance(scores.get(name), bool)
        ]
        missing = len(run_scores) - len(values)
        summary.metrics[name] = aggregate_values(name, values, missing)

    verdicts = [
        bool(scores[success_metric])
        for scores in run_scores
        if scores.get(success_metric) is not None
    ]
    summary.scored_runs = len(verdicts)
    summary.successes = sum(verdicts)
    summary.warnings = repetition_warnings(len(run_scores))
    return summary


def compare(
    control: ConditionSummary,
    treatment: ConditionSummary,
    directions: dict[str, str],
) -> list[Delta]:
    """Per-metric differences, with a guard against reading noise as a result."""
    deltas: list[Delta] = []
    for name in sorted(set(control.metrics) | set(treatment.metrics)):
        left = control.metrics.get(name)
        right = treatment.metrics.get(name)
        control_mean = left.mean if left else None
        treatment_mean = right.mean if right else None
        delta = Delta(
            name=name,
            control=control_mean,
            treatment=treatment_mean,
            direction=directions.get(name, "neutral"),
        )
        if control_mean is not None and treatment_mean is not None:
            delta.absolute = treatment_mean - control_mean
            if control_mean != 0:
                delta.relative = delta.absolute / abs(control_mean)
            noise = max(
                (left.stdev if left and left.stdev else 0.0),
                (right.stdev if right and right.stdev else 0.0),
            )
            # With no spread to compare against, the honest answer is
            # "unknown", not "yes". Two cases produce none:
            #
            #   * one repetition — there is nothing to take a spread over;
            #   * every repetition landing on the same value, which makes the
            #     noise floor exactly 0.0 and would wave *any* non-zero delta
            #     through as "beyond noise". Coarse metrics at low repetition
            #     counts hit this constantly (10,10 vs 1,1), and it is the most
            #     confident-looking wrong answer the comparison can give.
            has_spread = (left and left.n > 1) or (right and right.n > 1)
            delta.exceeds_spread = (
                abs(delta.absolute) > noise if has_spread and noise > 0 else None
            )
        deltas.append(delta)
    return deltas


def success_delta(
    control: ConditionSummary, treatment: ConditionSummary
) -> dict[str, Any]:
    """Difference in success rate, in percentage points."""
    left = control.success_rate
    right = treatment.success_rate
    if left is None or right is None:
        return {"control": left, "treatment": right, "points": None}
    return {
        "control": left,
        "treatment": right,
        "points": (right - left) * 100.0,
    }


def paired_by_case(
    control_runs: dict[str, bool | None], treatment_runs: dict[str, bool | None]
) -> list[dict[str, Any]]:
    """Per-case outcome for both arms — the matrix, not the average.

    This is usually more informative than any aggregate: it shows *where* a
    treatment helps, where it does nothing, and where it makes things worse. An
    average of +8 points hides "fixed four cases, broke two".
    """
    rows = []
    for case_id in sorted(set(control_runs) | set(treatment_runs)):
        left = control_runs.get(case_id)
        right = treatment_runs.get(case_id)
        if left is None or right is None:
            movement = "incomplete"
        elif left == right:
            movement = "unchanged"
        elif right:
            movement = "fixed"
        else:
            movement = "broken"
        rows.append(
            {
                "case_id": case_id,
                "control": left,
                "treatment": right,
                "movement": movement,
            }
        )
    return rows


def confidence_interval(
    values: list[float], z: float = 1.96
) -> tuple[float, float] | None:
    """Normal-approximation interval around the mean, or None below N=3.

    Under three observations the interval is wider than anything it could
    inform, so returning None is more honest than returning a number nobody
    should act on.
    """
    if len(values) < 3:
        return None
    mean = statistics.fmean(values)
    margin = z * (statistics.stdev(values) / math.sqrt(len(values)))
    return (mean - margin, mean + margin)


# --------------------------------------------------------------------------
# The ten-second answer
# --------------------------------------------------------------------------

# What a reader wants first, in order: did it work, what did it cost, how much
# work was it. Everything else belongs in the full metric table.
# Graded outcome first, then what it cost. `billed_tokens` rather than
# `total_tokens`: the latter excludes the prompt cache and once showed a
# full-harness arm using fewer tokens than a bare one while costing 71% more.
HEADLINE_METRICS = (
    "test_pass_rate",
    "cost_usd",
    "billed_tokens",
    "agent_steps",
    "wall_time_minutes",
)

# A success-rate move smaller than this is not worth a directional word at the
# top of the page whatever the design. Product judgement, not statistics — and
# it is a floor on the *wording*, never a claim of significance.
_MATERIAL_POINTS = 5.0


def evidence_strength(repetitions: int) -> str:
    """Prudent words for what a repetition count supports. Never "significant"."""
    if repetitions < EXPLORATORY_ONLY:
        return "exploratory"
    if repetitions < STRONGER_EVIDENCE:
        return "judgement"
    return "validation"


def _highlight(
    label: str, name: str, control: ConditionSummary, treatment: ConditionSummary
) -> dict[str, Any] | None:
    left = control.metrics.get(name)
    right = treatment.metrics.get(name)
    if not left or not right or left.mean is None or right.mean is None:
        return None
    # The reader needs to know which way is good: "10,712 → 7,223 tokens"
    # reads as an improvement, and for a cost it is — but the page used to
    # leave that to the reader, who supplied the direction from the label.
    return {
        "label": label,
        "metric": name,
        "control": left.mean,
        "treatment": right.mean,
        "direction": _direction_of(name),
    }


def _direction_of(name: str) -> str:
    """`higher_is_better` / `lower_is_better` / `neutral` for a registry score."""
    try:
        from harness_lab_app.scores import get_definition

        return str(get_definition(name).direction)
    except (KeyError, ImportError):
        return "neutral"


def _movements(case_matrix: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"fixed": 0, "broken": 0, "unchanged": 0, "incomplete": 0}
    for row in case_matrix:
        movement = str(row.get("movement", ""))
        if movement in counts:
            counts[movement] += 1
    return counts


def stop_reason_caveat(
    control_label: str,
    control_reasons: Sequence[str],
    treatment_label: str,
    treatment_reasons: Sequence[str],
) -> str | None:
    """Warn when the two arms stopped for different reasons.

    Effort and cost metrics only compare like with like if the arms ran under
    the same termination rule. An arm that ends on a completion signal after
    one invocation and an arm that burns its whole step budget are not two
    measurements of the same quantity: the gap between them is mostly the stop
    criterion, and reading it as a treatment effect credits the prompt's
    closing sentence with work the agent never did.
    """
    left = {reason for reason in control_reasons if reason}
    right = {reason for reason in treatment_reasons if reason}
    if not left or not right or left & right:
        return None
    return (
        f"The arms stopped for different reasons — {control_label} "
        f"({', '.join(sorted(left)).replace('_', ' ')}) against {treatment_label} "
        f"({', '.join(sorted(right)).replace('_', ' ')}). Effort and cost "
        "differences partly measure the stop criterion, not the work."
    )


# --------------------------------------------------------------------------
# Validity — the thing a reader must meet before the verdict
# --------------------------------------------------------------------------

# Three different accidents — a treatment that never fired, runs that never ran,
# a control with nowhere to go — make the same claim about an experiment: *this
# comparison could not have measured the thing it names*. They share an audience,
# a placement and a consequence, so they share a vocabulary here rather than
# arriving as three unrelated flags nobody can render together.
VALID = "valid"
DEGRADED = "degraded"
INVALID = "invalid"

# The order a reader should meet these in when several hold at once.
_ISSUE_ORDER = (
    "treatment_not_activated",
    "infrastructure_failures",
    "no_headroom",
    "mixed_treatment",
    "design_objection",
)


@dataclass
class ValidityIssue:
    """One reason this comparison could not have measured what it names."""

    code: str
    severity: str
    detail: str
    remedy: str
    arm: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "arm": self.arm,
            "detail": self.detail,
            "remedy": self.remedy,
        }


def _skill_usage_issues(
    skill_usage: Mapping[str, Mapping[str, Any]],
    *,
    treatment_label: str | None = None,
) -> list[ValidityIssue]:
    """Turn the per-arm invocation observation into issues (ADR-0041).

    Only the *treatment* arm at zero invalidates. A control arm that opened
    nothing is usually correct — an ablated arm has no skills to open — and the
    asymmetry is by design in ``score_service.build_context``.
    """
    issues: list[ValidityIssue] = []
    for label, usage in skill_usage.items():
        skills = list(usage.get("skills") or [])
        runs = int(usage.get("runs") or 0)
        invoked = int(usage.get("invoked") or 0)
        if not skills or runs == 0:
            continue
        # A full-harness arm is "about" the whole catalog, so naming every skill
        # would bury the sentence under twenty-five quoted strings.
        many = len(skills) > 3
        names = ", ".join(f"'{name}'" for name in skills)
        never = (
            f"none of its {len(skills)} skills was invoked"
            if many
            else f"{names} was never invoked"
        )
        mixed = f"its {len(skills)} skills were invoked" if many else f"{names} invoked"
        requested = bool(usage.get("requested"))
        if invoked == 0:
            issues.append(
                ValidityIssue(
                    code="treatment_not_activated",
                    severity=INVALID if label == treatment_label else DEGRADED,
                    arm=label,
                    detail=(
                        f"{label}: {never} in its {runs} run(s)"
                        + (" although the prompt asked for it" if requested else "")
                        + " — this arm measured the skill's availability, not its use."
                    ),
                    remedy=(
                        "Check the skill's own instructions before reading the result."
                        if requested
                        else "Ask for it in the design ('Enforce skill use'), or make "
                        "the skill's triggers match the task, before reading the result."
                    ),
                )
            )
        elif invoked < runs:
            issues.append(
                ValidityIssue(
                    code="mixed_treatment",
                    severity=DEGRADED,
                    arm=label,
                    detail=(
                        f"{label}: {mixed} in {invoked} of {runs} run(s) — the "
                        "arm mixes runs with and without the treatment."
                    ),
                    remedy=(
                        "Read the per-run skill_invoked observation before the "
                        "aggregate."
                    ),
                )
            )
    return issues


def _run_health_issues(
    run_health: Mapping[str, Mapping[str, Any]],
) -> list[ValidityIssue]:
    """Flag an arm whose runs measured the connection rather than the subject.

    A run that ended in ``harness_error`` and was archived at exit 0 entered the
    corpus as a model scoring zero. Flagging on read rather than rewriting the
    run's recorded state keeps the record of what the lab believed at the time.
    """
    issues: list[ValidityIssue] = []
    for label, health in run_health.items():
        runs = int(health.get("runs") or 0)
        if not runs:
            continue
        harness_error = int(health.get("harness_error") or 0)
        failed = int(health.get("failed") or 0)
        # A failed run usually *is* the harness_error run; without a union count
        # from the caller the larger of the two is the safe lower bound.
        unhealthy = int(health.get("unhealthy") or max(harness_error, failed))
        if not unhealthy:
            continue
        starved = int(health.get("scored_runs") or 0) == 0
        issues.append(
            ValidityIssue(
                code="infrastructure_failures",
                severity=INVALID if starved or unhealthy * 2 >= runs else DEGRADED,
                arm=label,
                detail=(
                    f"{label}: {unhealthy} of {runs} run(s) ended in an "
                    f"infrastructure failure ({harness_error} harness error(s), "
                    f"{failed} failed)"
                    + (" and the arm has no scored run left" if starved else "")
                    + " — these runs measured the connection, not the subject."
                ),
                remedy=(
                    f"Re-run the affected repetitions of {label} before reading "
                    "the result."
                ),
            )
        )
    return issues


def _ceiling_issue(
    control: ConditionSummary, success: Mapping[str, Any] | None
) -> ValidityIssue | None:
    """Flag a control arm that already passes everything.

    A control at 100% leaves the design able to detect a regression and nothing
    else, because an improvement above 100% is unrepresentable. Below
    ``EXPLORATORY_ONLY`` this stays quiet: a 1/1 control is evidence of one run,
    not of a ceiling.
    """
    if control.scored_runs < EXPLORATORY_ONLY:
        return None
    if control.successes != control.scored_runs:
        return None
    points = (success or {}).get("points")
    regressed = points is not None and points < 0
    return ValidityIssue(
        code="no_headroom",
        severity=(
            DEGRADED
            if regressed or control.scored_runs < STRONGER_EVIDENCE
            else INVALID
        ),
        arm=control.label,
        detail=(
            f"{control.label} passed every one of its {control.scored_runs} "
            "scored run(s), so this design has no headroom"
            + (
                ", though the regression it did measure was still detectable."
                if regressed
                else " — it could only ever have shown no change or a regression."
            )
        ),
        remedy=(
            "Pick a case the control does not already solve, or score on a "
            "metric with room to move."
        ),
    )


def assess_validity(
    *,
    mode: str,
    control: ConditionSummary | None = None,
    treatment: ConditionSummary | None = None,
    skill_usage: Mapping[str, Mapping[str, Any]] | None = None,
    run_health: Mapping[str, Mapping[str, Any]] | None = None,
    success: Mapping[str, Any] | None = None,
    design_notes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Can this experiment answer the question it asks?

    Returns ``{"status": valid|degraded|invalid, "issues": [...]}``. A mode that
    makes no causal claim has nothing to invalidate, and ``subject_validation``
    reports its issues capped at ``degraded``: it has no control arm, so a
    ceiling is meaningless and an unopened skill is information about that arm
    rather than a broken comparison.
    """
    if mode in ("discovery", "cross_repo"):
        return {"status": VALID, "issues": []}

    issues = _skill_usage_issues(
        skill_usage or {},
        treatment_label=treatment.label if treatment else None,
    )
    issues.extend(_run_health_issues(run_health or {}))
    if mode == "harness_evaluation" and control is not None:
        ceiling = _ceiling_issue(control, success)
        if ceiling:
            issues.append(ceiling)
    for note in design_notes or []:
        issues.append(
            ValidityIssue(
                code="design_objection",
                severity=DEGRADED,
                arm=None,
                detail=str(note),
                remedy="Revisit the design before treating this as a finding.",
            )
        )

    if mode == "subject_validation":
        for issue in issues:
            issue.severity = DEGRADED

    issues.sort(
        key=lambda issue: (
            0 if issue.severity == INVALID else 1,
            _ISSUE_ORDER.index(issue.code)
            if issue.code in _ISSUE_ORDER
            else len(_ISSUE_ORDER),
        )
    )
    status = INVALID
    if not any(issue.severity == INVALID for issue in issues):
        status = DEGRADED if issues else VALID
    return {"status": status, "issues": [issue.as_dict() for issue in issues]}


def skill_usage_caveats(skill_usage: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """What to say when an arm's skills were not actually opened (ADR-0041).

    ``skill_usage`` maps an arm label to ``{"skills", "runs", "invoked",
    "requested"}`` — the skills the arm is about, how many of its runs carry a
    ``skill_invoked`` observation, how many of those opened at least one, and
    whether the prompt asked for it. An arm with no observation says nothing.

    A with-skills arm at zero is the whole point: its number compared the
    skill's *availability*, and the reader must learn that before the delta.
    Kept as the sentence-level view of :func:`_skill_usage_issues`, so the prose
    lives in exactly one place.
    """
    return [
        f"{issue.detail} {issue.remedy}"
        if issue.code == "treatment_not_activated"
        else issue.detail
        for issue in _skill_usage_issues(skill_usage)
    ]


def _compose_verdict(
    *,
    mode: str,
    cases: int,
    repetitions: int,
    control: ConditionSummary | None = None,
    treatment: ConditionSummary | None = None,
    success: dict[str, Any] | None = None,
    case_matrix: list[dict[str, Any]] | None = None,
    stop_reasons: Mapping[str, Sequence[str]] | None = None,
    subject: str = "",
    arms: Sequence[ConditionSummary] | None = None,
    skill_usage: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """The answer an experiment page leads with, in words the design supports.

    Three rules hold this together, and they are the reason this is a function
    rather than a template in the frontend:

    - **A mode that cannot make a causal claim never gets one.** Discovery
      describes; cross-repo reports per repository; subject validation reports
      a pass rate per arm. Only ``harness_evaluation`` is allowed a directional
      verdict, and ``causal`` says so on every payload.
    - **No invented significance.** The vocabulary is exploratory / useful
      comparison / stronger evidence / within observed variance / improvement
      observed — the wording moves with the repetition count, and no p-value is
      ever implied.
    - **The aggregate never hides the matrix.** An experiment that gained eight
      points and broke two cases says both, in the headline block.
    """
    strength = evidence_strength(repetitions)
    usage_caveats = skill_usage_caveats(skill_usage or {})
    # The "\u00d7" is deliberate: this string is rendered verbatim as the
    # evidence line of the experiment page.
    evidence = (
        f"{cases} case{'s' if cases != 1 else ''} \u00d7 "
        f"{repetitions} repetition{'s' if repetitions != 1 else ''}"
    )
    base: dict[str, Any] = {
        "causal": False,
        "strength": strength,
        "evidence": evidence,
        "highlights": [],
        "movements": _movements(case_matrix or []),
        "caveats": [],
    }

    if mode == "discovery":
        return {
            **base,
            "headline": "Exploratory — no comparison",
            "claim": (
                "Discovery describes how this repository behaves today. It has "
                "no control arm, compares nothing, and supports no causal claim."
            ),
            "caveats": [
                "Use these runs to derive cases and findings, not to conclude "
                "that anything improved."
            ],
        }

    if mode == "cross_repo":
        return {
            **base,
            "headline": "Per-repository result",
            "claim": (
                "This experiment holds the harness fixed and varies the "
                "repository, so it shows where the harness holds and where it "
                "degrades — not that one repository is better than another."
            ),
            "caveats": [
                "Scores from different repositories are not pooled. Read this "
                "experiment one repository at a time."
            ],
        }

    if mode == "subject_validation":
        # One line per arm, and never a line across them. Pooling "8 of 9 under
        # claude" with "4 of 9 under opencode" into "12 of 18" would describe a
        # setup nobody runs, and is the exact composite this lab refuses
        # everywhere else (ADR-0040).
        named = subject or "This subject"
        scored = [arm for arm in (arms or []) if arm.scored_runs]
        # Facts about the design, not about the data, so they hold with zero
        # runs scored — which is exactly when a reader is most likely to
        # mislabel what this page is.
        structural = [
            "No arm was ablated, so this experiment says nothing about the "
            "harness — only how reliably this subject completed its cases "
            "under each harness and model.",
            "Rates from different arms are not pooled. A combined number "
            "would describe a configuration nobody runs.",
        ]
        highlights = [
            {
                "label": arm.label,
                "metric": "success_rate",
                "successes": arm.successes,
                "scored_runs": arm.scored_runs,
                "rate": arm.success_rate,
            }
            for arm in scored
        ]
        if not scored:
            return {
                **base,
                "headline": "Not enough evidence yet",
                "claim": (
                    f"No run of {named} has produced a validation verdict yet, so "
                    "there is no pass rate to report."
                ),
                "caveats": structural,
            }
        if len(scored) == 1:
            only = scored[0]
            claim = (
                f"{named} passed {only.successes} of {only.scored_runs} scored "
                f"run(s) under {only.label}, over {evidence}."
            )
        else:
            # Every arm, best first. Naming only the extremes reads as a
            # comparison, and on a tie the two extremes are the same arm.
            ordered = sorted(
                scored, key=lambda arm: (arm.success_rate or 0.0), reverse=True
            )
            parts = [
                f"{arm.successes} of {arm.scored_runs} under {arm.label}"
                for arm in ordered
            ]
            listed = ", ".join(parts[:-1]) + f" and {parts[-1]}"
            claim = (
                f"{named} passed {listed}, over {evidence}. Read each arm on its own."
            )
        return {
            **base,
            "headline": "Reliability per arm",
            "claim": claim,
            "highlights": highlights,
            "caveats": [
                *usage_caveats,
                *structural,
                f"{repetitions} repetition(s) per arm — {strength}.",
            ],
        }

    if control is None or treatment is None or not success:
        return {
            **base,
            "headline": "Not enough evidence yet",
            "claim": "No completed comparison has been scored for this experiment.",
        }

    highlights = [
        {
            "label": "Success",
            "metric": "success_rate",
            "control": success.get("control"),
            "treatment": success.get("treatment"),
            "direction": "higher_is_better",
        }
    ]
    for name in HEADLINE_METRICS:
        found = _highlight(
            name.replace("_", " ").capitalize(), name, control, treatment
        )
        if found:
            highlights.append(found)

    movements = base["movements"]
    # First, before any delta: a treatment that never happened is not a
    # treatment, whatever the numbers underneath say.
    caveats: list[str] = list(usage_caveats)
    confound = (
        stop_reason_caveat(
            control.label,
            stop_reasons.get(control.label, ()),
            treatment.label,
            stop_reasons.get(treatment.label, ()),
        )
        if stop_reasons
        else None
    )
    if confound:
        caveats.append(confound)
    if repetitions < USEFUL_COMPARISON:
        caveats.append(
            f"{repetitions} repetition{'s' if repetitions != 1 else ''} per arm — "
            f"{strength}. Agents are stochastic; this cannot separate a real "
            "difference from run-to-run variance."
        )
    elif repetitions < STRONGER_EVIDENCE:
        caveats.append(
            f"{repetitions} repetitions per arm — a {strength}, not a validation. "
            f"{STRONGER_EVIDENCE} or more makes it one."
        )
    if movements["broken"]:
        caveats.append(
            f"{movements['broken']} case(s) that passed under {control.label} "
            f"fail under {treatment.label}. Read the case matrix before acting "
            "on the aggregate."
        )
    if movements["incomplete"]:
        caveats.append(
            f"{movements['incomplete']} case(s) have no paired outcome yet, so "
            "they are absent from the comparison."
        )

    points = success.get("points")
    if points is None:
        return {
            **base,
            "causal": True,
            "headline": "Not enough evidence yet",
            "claim": (
                "One of the arms has no scored run, so there is no success rate "
                "to compare."
            ),
            "highlights": highlights,
            "caveats": caveats,
        }

    if abs(points) < 1e-9:
        headline = "No difference observed"
        claim = (
            f"{treatment.label} matched {control.label} on success rate over "
            f"{evidence}."
        )
    else:
        word = "improvement" if points > 0 else "regression"
        material = abs(points) >= _MATERIAL_POINTS
        if repetitions >= USEFUL_COMPARISON and material:
            headline = f"Likely {word}"
            claim = (
                f"{treatment.label} moved success by {points:+.0f} points against "
                f"{control.label} over {evidence} — a {strength}."
            )
        else:
            headline = f"{word.capitalize()} observed"
            claim = (
                f"{treatment.label} moved success by {points:+.0f} points against "
                f"{control.label}, but over {evidence} that sits within the "
                "variance this design can distinguish."
            )
            caveats.insert(
                0, "Within observed variance — treat as a signal, not a result."
            )

    return {
        **base,
        "causal": True,
        "headline": headline,
        "claim": claim,
        "highlights": highlights,
        "caveats": caveats,
    }


def _apply_validity(
    answer: dict[str, Any], validity: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Lead with invalidity; keep the measurement.

    ADR-0041 refused to *withhold* a verdict when the treatment never fired —
    the numbers are true, and blocking hides them. This does not reverse that
    decision, it relocates it: the sentence the page would have led with moves
    to ``observed``, intact, and what a reader meets first becomes the reason
    it cannot be read as an answer. A caveat under a confident headline is the
    failure mode this exists to end.
    """
    headline = str(answer.get("headline", "")).lower()
    tone = "neutral"
    if "improvement" in headline:
        tone = "better"
    elif "regression" in headline:
        tone = "worse"

    issues = list((validity or {}).get("issues") or [])
    if validity and validity.get("status") == INVALID and issues:
        lead, *rest = issues
        answer["observed"] = answer.get("claim", "")
        answer["headline"] = "Comparison not valid"
        answer["claim"] = (
            f"{lead.get('detail', '')} The numbers below describe what happened, "
            "not whether the harness helped."
        ).strip()
        leading = [str(lead.get("remedy") or "")] + [
            f"{issue.get('detail')} {issue.get('remedy')}".strip() for issue in rest
        ]
        leading = [text for text in leading if text]
        answer["caveats"] = leading + [
            caveat for caveat in (answer.get("caveats") or []) if caveat not in leading
        ]
        tone = "invalid"

    answer["tone"] = tone
    return answer


def verdict(
    *, validity: Mapping[str, Any] | None = None, **kwargs: Any
) -> dict[str, Any]:
    """The answer an experiment page leads with — see :func:`_compose_verdict`.

    ``validity`` comes from :func:`assess_validity`. When it says the comparison
    is invalid, the composed claim is preserved under ``observed`` and the
    headline is replaced by the reason. ``tone`` is always set, so the frontend
    reads a field rather than sniffing English out of the headline.
    """
    return _apply_validity(_compose_verdict(**kwargs), validity)
