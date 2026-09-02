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
EXPLORATORY_ONLY = 2
USEFUL_COMPARISON = 5
STRONGER_EVIDENCE = 10


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
            "separate a real difference from run-to-run variance."
        ]
    if n < USEFUL_COMPARISON:
        return [
            f"{n} repetitions — exploratory comparison. Use {USEFUL_COMPARISON} or "
            "more before treating a delta as a finding."
        ]
    if n < STRONGER_EVIDENCE:
        return [
            f"{n} repetitions — a useful comparison. {STRONGER_EVIDENCE} or more "
            "makes it a strong one."
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
HEADLINE_METRICS = ("cost_usd", "agent_steps", "wall_time_minutes", "total_tokens")

# A success-rate move smaller than this is not worth a directional word at the
# top of the page whatever the design. Product judgement, not statistics — and
# it is a floor on the *wording*, never a claim of significance.
_MATERIAL_POINTS = 5.0


def evidence_strength(repetitions: int) -> str:
    """Prudent words for what a repetition count supports. Never "significant"."""
    if repetitions < EXPLORATORY_ONLY:
        return "exploratory"
    if repetitions < USEFUL_COMPARISON:
        return "exploratory comparison"
    if repetitions < STRONGER_EVIDENCE:
        return "useful comparison"
    return "stronger evidence"


def _highlight(
    label: str, name: str, control: ConditionSummary, treatment: ConditionSummary
) -> dict[str, Any] | None:
    left = control.metrics.get(name)
    right = treatment.metrics.get(name)
    if not left or not right or left.mean is None or right.mean is None:
        return None
    return {
        "label": label,
        "metric": name,
        "control": left.mean,
        "treatment": right.mean,
    }


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


def verdict(
    *,
    mode: str,
    cases: int,
    repetitions: int,
    control: ConditionSummary | None = None,
    treatment: ConditionSummary | None = None,
    success: dict[str, Any] | None = None,
    case_matrix: list[dict[str, Any]] | None = None,
    stop_reasons: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """The answer an experiment page leads with, in words the design supports.

    Three rules hold this together, and they are the reason this is a function
    rather than a template in the frontend:

    - **A mode that cannot make a causal claim never gets one.** Discovery
      describes; cross-repo reports per repository. Only ``harness_evaluation``
      is allowed a directional verdict, and ``causal`` says so on every payload.
    - **No invented significance.** The vocabulary is exploratory / useful
      comparison / stronger evidence / within observed variance / improvement
      observed — the wording moves with the repetition count, and no p-value is
      ever implied.
    - **The aggregate never hides the matrix.** An experiment that gained eight
      points and broke two cases says both, in the headline block.
    """
    strength = evidence_strength(repetitions)
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
        }
    ]
    for name in HEADLINE_METRICS:
        found = _highlight(
            name.replace("_", " ").capitalize(), name, control, treatment
        )
        if found:
            highlights.append(found)

    movements = base["movements"]
    caveats: list[str] = []
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
