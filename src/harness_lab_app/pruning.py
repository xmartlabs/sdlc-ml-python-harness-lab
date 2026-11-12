"""Is a harness item removable? Leave-one-out with an equivalence margin (ADR-0076).

The per-step reward says *when* progress happened and what was in context at
the time. That is a hint, not a cause. The only way to learn whether a skill or
a governance document earns its tokens is to run the same cases without it and
compare — an ablation. This module reads that comparison and answers one
question per item: **can it go?**

"No difference observed" is not the answer. With a handful of runs almost
nothing shows a difference, so a rule that removed whatever showed none would
delete the harness one under-powered experiment at a time. The rule here is
**non-inferiority**: an item is removable only when the *whole* interval for
"what removing it does" stays inside a margin declared up front:

* success rate: the treatment (item removed) may lose at most
  ``success_margin`` (default 5 points) — the lower end of the paired,
  case-clustered 95% interval must stay above ``-success_margin``;
* every lower-is-better metric given (``cost_to_resolve_usd`` by default): the
  upper end of the paired interval may not exceed ``relative_margin`` (default
  20%) of the control's mean;
* every higher-is-better metric given (``progress_auc``): the lower end may not
  fall below ``-relative_margin`` of the control's mean.

Any interval that sits wholly on the wrong side of a margin is **keep**; any
that straddles one is **inconclusive** — run more cases. Nothing here says
*significant* (ADR-0053, ADR-0064); it says what the intervals support.

Harness value depends on the model (ADR-0066): run a pruning family per model
tier and remove an item only for the tiers where it is removable.
"""

from __future__ import annotations

import random
import statistics as stats
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from harness_lab_app.statistics import _quantile, paired_bootstrap_interval

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

REMOVABLE = "removable"
KEEP = "keep"
INCONCLUSIVE = "inconclusive"

DEFAULT_SUCCESS_MARGIN = 0.05
DEFAULT_RELATIVE_MARGIN = 0.20
# Below this many cases both arms ran, the between-case spread is not something
# a bootstrap can estimate, and no item is called removable however clean the
# runs look (the same floor the factorial reading uses).
MIN_PAIRED_CASES = 3

BOOTSTRAP_SEED = 76
BOOTSTRAP_RESAMPLES = 2_000

# The metrics the pruning verdict reads, with the direction that is better.
LOWER_IS_BETTER = "lower"
HIGHER_IS_BETTER = "higher"
PRUNING_METRICS: dict[str, str] = {
    "cost_to_resolve_usd": LOWER_IS_BETTER,
    "progress_auc": HIGHER_IS_BETTER,
}


def paired_mean_interval(
    control: Mapping[str, Sequence[float]],
    treatment: Mapping[str, Sequence[float]],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float] | None:
    """95% interval for ``mean(treatment) - mean(control)``, resampled by case.

    The numeric twin of :func:`statistics.paired_bootstrap_interval`: cases are
    drawn once per resample for both arms (that is what keeps it paired), then
    each arm's values within a drawn case. None when no case has values in both.
    """
    cases = sorted(
        case
        for case in set(control) & set(treatment)
        if control[case] and treatment[case]
    )
    if not cases:
        return None
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(resamples):
        drawn = rng.choices(cases, k=len(cases))
        c_values: list[float] = []
        t_values: list[float] = []
        for case in drawn:
            c_values.extend(rng.choices(list(control[case]), k=len(control[case])))
            t_values.extend(rng.choices(list(treatment[case]), k=len(treatment[case])))
        deltas.append(stats.fmean(t_values) - stats.fmean(c_values))
    deltas.sort()
    return (_quantile(deltas, 0.025), _quantile(deltas, 0.975))


@dataclass(frozen=True)
class RemovalVerdict:
    item: str
    decision: str
    reason: str
    paired_cases: int
    success_interval: tuple[float, float] | None
    metric_intervals: dict[str, dict[str, Any]] = field(default_factory=dict)
    margins: dict[str, float] = field(default_factory=dict)
    # Required metrics (PRUNING_METRICS) with no case paired across both arms.
    # Any one of them holds a removal open: success alone is not the evidence
    # the rule asks for.
    missing_metrics: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _paired(
    control: Mapping[str, Sequence[Any]], treatment: Mapping[str, Sequence[Any]]
) -> int:
    return sum(
        1 for case in set(control) & set(treatment) if control[case] and treatment[case]
    )


def assess_removal(
    *,
    item: str,
    control_success: Mapping[str, Sequence[bool]],
    treatment_success: Mapping[str, Sequence[bool]],
    control_metrics: Mapping[str, Mapping[str, Sequence[float]]] | None = None,
    treatment_metrics: Mapping[str, Mapping[str, Sequence[float]]] | None = None,
    success_margin: float = DEFAULT_SUCCESS_MARGIN,
    relative_margin: float = DEFAULT_RELATIVE_MARGIN,
) -> RemovalVerdict:
    """Decide whether ``item`` can be removed, from a control/ablation pair.

    ``control`` is the harness with the item; ``treatment`` is the same harness
    without it. Success is per case, per repetition. Metrics are per case, per
    repetition, keyed by score name (see :data:`PRUNING_METRICS`).
    """
    margins = {"success_rate": success_margin, "relative": relative_margin}
    paired = _paired(control_success, treatment_success)
    success_ci = paired_bootstrap_interval(control_success, treatment_success)

    keep_reasons: list[str] = []
    open_reasons: list[str] = []

    if success_ci is None:
        open_reasons.append("no case was scored by both arms")
    elif success_ci[1] < -success_margin:
        keep_reasons.append(
            f"success rate drops by {-success_ci[1]:.0%} to {-success_ci[0]:.0%} "
            "without it"
        )
    elif success_ci[0] < -success_margin:
        open_reasons.append(
            f"success interval [{success_ci[0]:+.0%}, {success_ci[1]:+.0%}] "
            f"reaches past the -{success_margin:.0%} margin"
        )

    metric_intervals: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for name, direction in PRUNING_METRICS.items():
        control_values = (control_metrics or {}).get(name) or {}
        treatment_values = (treatment_metrics or {}).get(name) or {}
        interval = paired_mean_interval(control_values, treatment_values)
        if interval is None:
            # Not measured on any case both arms ran (old runs without
            # snapshots, an arm that never resolved). Recorded, never skipped:
            # skipping it let a removal rest on the success rate alone.
            missing.append(name)
            continue
        baseline_values = [v for values in control_values.values() for v in values]
        baseline = stats.fmean(baseline_values) if baseline_values else 0.0
        if baseline == 0.0 and interval == (0.0, 0.0):
            # Both arms measured at exactly zero (a self-hosted model is free).
            # That is a measurement, and it says removal changed nothing; a
            # margin of 0% of 0 would otherwise hold the verdict open forever.
            metric_intervals[name] = {
                "interval": interval,
                "control_mean": baseline,
                "bound": 0.0,
                "direction": direction,
                "note": "both arms at zero",
            }
            continue
        bound = abs(baseline) * relative_margin
        metric_intervals[name] = {
            "interval": interval,
            "control_mean": baseline,
            "bound": bound,
            "direction": direction,
        }
        low, high = interval
        if direction == LOWER_IS_BETTER:
            if low > bound:
                keep_reasons.append(
                    f"{name} rises by {low:+.3g} to {high:+.3g} without it "
                    f"(margin {bound:.3g})"
                )
            elif high > bound:
                open_reasons.append(
                    f"{name} interval [{low:+.3g}, {high:+.3g}] reaches past "
                    f"the +{bound:.3g} margin"
                )
        else:
            if high < -bound:
                keep_reasons.append(
                    f"{name} falls by {-high:.3g} to {-low:.3g} without it "
                    f"(margin {bound:.3g})"
                )
            elif low < -bound:
                open_reasons.append(
                    f"{name} interval [{low:+.3g}, {high:+.3g}] reaches past "
                    f"the -{bound:.3g} margin"
                )

    if keep_reasons:
        decision, reason = KEEP, "; ".join(keep_reasons)
    elif paired < MIN_PAIRED_CASES:
        decision = INCONCLUSIVE
        reason = (
            f"only {paired} case(s) paired; at least {MIN_PAIRED_CASES} are "
            "needed before anything is called removable"
        )
    elif missing:
        decision = INCONCLUSIVE
        reason = (
            f"{', '.join(missing)} not measured on any paired case — a removal "
            "needs every metric inside its margin, not the success rate alone "
            "(are these runs from before per-step snapshots?)"
        )
    elif open_reasons:
        decision, reason = INCONCLUSIVE, "; ".join(open_reasons) + " — run more cases"
    else:
        decision = REMOVABLE
        reason = (
            "every interval stays inside its margin: removing it costs at most "
            f"{success_margin:.0%} of success and {relative_margin:.0%} of the "
            "control on each metric"
        )
    return RemovalVerdict(
        item=item,
        decision=decision,
        reason=reason,
        paired_cases=paired,
        success_interval=success_ci,
        metric_intervals=metric_intervals,
        margins=margins,
        missing_metrics=missing,
    )
