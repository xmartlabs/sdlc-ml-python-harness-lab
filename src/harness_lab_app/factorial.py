"""Harness x model analysis: does the harness help less on stronger models? (ADR-0066)

``harness_evaluation`` holds the model fixed so a difference can be attributed to
the harness. That design cannot answer the question 2026 keeps asking — *do
stronger models need less harness?* — because the answer is an **interaction**:
how the harness effect changes as the model changes. This module reads a full
grid of harness levels x models and reports:

- the harness effect per model: each level against the first (lowest) level, in
  percentage points, with a case-clustered 95% interval;
- the interaction: the effect of the top level on the last model minus its
  effect on the first, with its own interval;
- each cell's pass rate and, when costs are given, cost per resolved task.

Every interval comes from one paired, case-clustered bootstrap: a resample draws
the *cases* once and applies the same draw to every cell, then resamples each
cell's repetitions within the drawn case. All cells share a case's difficulty,
which is why resampling them independently would understate the uncertainty.

The reading is a sentence the intervals support, never more: an interaction is
"established" only when its interval excludes zero, and nothing here says
*significant* (ADR-0053, ADR-0064).
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Mapping, Sequence
from typing import Any

from harness_lab_app.statistics import _newcombe_interval

Cell = tuple[str, str]  # (harness level, model)
Outcomes = Mapping[str, Sequence[bool]]  # case id → pass/fail per repetition

BOOTSTRAP_SEED = 66
BOOTSTRAP_RESAMPLES = 2_000
# Below this many paired cases there is no between-case variation worth the
# name to resample, so an interaction is never called established, however
# large it looks: a bootstrap over one unanimous case returns that case's value
# two thousand times and reads as certainty.
MIN_CASES_TO_ESTABLISH = 3

# The axes a harness level can be defined on (one of them, never two).
HARNESS_AXES = (
    "harness_source",
    "prompt_variant",
    "skills_disabled",
    "skills_extra",
    "skills_invoke",
    "harness",
)


def _key(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(sorted(str(item) for item in value))
    return value


LADDER_AXIS = "harness_level"


def _varying_harness_fields(conditions: list[dict[str, Any]]) -> list[str]:
    return [
        axis
        for axis in HARNESS_AXES
        if len({_key((c.get("overrides") or {}).get(axis)) for c in conditions}) > 1
    ]


def _signature(condition: dict[str, Any], fields: list[str]) -> tuple:
    overrides = condition.get("overrides") or {}
    return tuple(_key(overrides.get(field)) for field in fields)


# A ladder longer than this is checked by brute force over orderings, which is
# only cheap for the handful of rungs an experiment actually declares.
_MAX_LADDER_RUNGS = 6


def _is_nested_ladder(conditions: list[dict[str, Any]], fields: list[str]) -> bool:
    """Can the distinct harness levels be ordered so each rung changes exactly one
    field from the rung before it? Three or more rungs are needed: with two, two
    fields moving together cannot be told apart (a confound).

    Any such ordering will do; the levels are not assumed to sort themselves —
    ordering by "how many fields are set" ties and depends on set iteration order.
    """
    levels = sorted({_signature(c, fields) for c in conditions}, key=repr)
    if not 3 <= len(levels) <= _MAX_LADDER_RUNGS:
        return False

    def one_step(lower: tuple, upper: tuple) -> bool:
        return sum(1 for a, b in zip(lower, upper, strict=True) if a != b) == 1

    return any(
        all(one_step(lower, upper) for lower, upper in itertools.pairwise(order))
        for order in itertools.permutations(levels)
    )


def harness_axis(conditions: list[dict[str, Any]]) -> str | None:
    """The harness factor the levels vary on: one field, or a nested ladder of
    several (``harness_level``, ADR-0066). None when neither applies."""
    varying = _varying_harness_fields(conditions)
    if len(varying) == 1:
        return varying[0]
    if len(varying) > 1 and _is_nested_ladder(conditions, varying):
        return LADDER_AXIS
    return None


# How a ladder level reads: the harness source in plain words, then what the
# level takes away or adds. Field names and full skill lists are for the record,
# not for a table cell.
_SOURCE_WORDS = {
    "bare": "no harness",
    "lab_root": "lab harness",
    "native": "repo's own harness",
    "injected": "lab harness, injected",
}


def _short(field: str, value: Any) -> str:
    raw = getattr(value, "value", value)
    if field == "harness_source":
        return _SOURCE_WORDS.get(str(raw), str(raw))
    if isinstance(value, (list, tuple)):
        n = len(value)
        noun = "skill" if n == 1 else "skills"
        if field == "skills_disabled":
            return f"{n} {noun} off"
        if field in ("skills_extra", "skills_invoke"):
            verb = "added" if field == "skills_extra" else "invoked"
            return f"{n} {noun} {verb}"
        return f"{field}: {n}"
    return f"{field}={raw}"


def level_key(condition: dict[str, Any], conditions: list[dict[str, Any]]) -> str:
    """The level's technical identity — field=value, as a design is written.

    Used for validation and error messages, where the reader has to fix a
    field; :func:`level_of` is the label a results table shows.
    """
    varying = _varying_harness_fields(conditions)
    overrides = condition.get("overrides") or {}
    parts = []
    for field in varying:
        value = overrides.get(field)
        if value in (None, "", []):
            continue
        raw = getattr(value, "value", value)
        shown = f"{len(raw)} item(s)" if isinstance(raw, (list, tuple)) else str(raw)
        parts.append(f"{field}={shown}")
    return ", ".join(parts) or "(default)"


def level_of(condition: dict[str, Any], conditions: list[dict[str, Any]]) -> str:
    """This condition's harness level, labelled for a reader."""
    varying = _varying_harness_fields(conditions)
    overrides = condition.get("overrides") or {}
    if len(varying) == 1:
        field = varying[0]
        value = overrides.get(field)
        if field == "harness_source" or isinstance(value, (list, tuple)):
            return _short(field, value) if value not in (None, "") else "(default)"
        return _level_label(value)
    parts = [
        _short(field, overrides.get(field))
        for field in varying
        if overrides.get(field) not in (None, "", [])
    ]
    label = ", ".join(parts) or "(default)"
    # "lab harness, 31 skills off" reads as what it is: governance without skills.
    if getattr(
        overrides.get("harness_source"), "value", overrides.get("harness_source")
    ) == "lab_root" and overrides.get("skills_disabled"):
        label = f"governance only ({_short('skills_disabled', overrides['skills_disabled'])})"
    return label


def _level_label(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(sorted(str(item) for item in value)) or "(none)"
    return "(default)" if value in (None, "") else str(value)


def grid_problems(conditions: list[dict[str, Any]]) -> list[str]:
    """Why these conditions are not a full harness x model grid (empty when they are)."""
    problems: list[str] = []
    models = {(c.get("overrides") or {}).get("executor_model") for c in conditions}
    if None in models or "" in models:
        problems.append("Every condition must name its executor model.")
    if len(models - {None, ""}) < 2:
        problems.append("A harness x model grid needs at least two models.")
    varying = _varying_harness_fields(conditions)
    if not varying:
        problems.append(
            "No harness field varies, so there is no harness level to compare. Vary "
            "exactly one of: " + ", ".join(HARNESS_AXES) + "."
        )
    elif len(varying) > 1 and not _is_nested_ladder(conditions, varying):
        problems.append(
            "Exactly one harness field may vary across levels; these vary together: "
            + ", ".join(varying)
            + ". Pick one harness factor per experiment, or build a ladder of three "
            "or more levels where each adds one field to the level below "
            "(none → governance → governance + skills)."
        )
    others = {"target_repo_id", "target_ref", "planner_model", "max_steps"}
    confounds = sorted(
        field
        for field in others
        if len({_key((c.get("overrides") or {}).get(field)) for c in conditions}) > 1
    )
    if confounds:
        problems.append(
            "Only the harness and the model may vary; these vary too: "
            + ", ".join(confounds)
            + "."
        )
    if problems:
        return problems
    seen: dict[tuple[str, str], str] = {}
    for condition in conditions:
        overrides = condition.get("overrides") or {}
        cell = (level_key(condition, conditions), str(overrides["executor_model"]))
        if cell in seen:
            problems.append(
                f"The cell {cell[0]} x {cell[1]} appears twice "
                f"('{seen[cell]}' and '{condition.get('label')}')."
            )
        seen[cell] = str(condition.get("label"))
    levels = sorted({level for level, _ in seen})
    models_sorted = sorted({model for _, model in seen})
    if len(levels) < 2:
        problems.append("A harness x model grid needs at least two harness levels.")
    for level in levels:
        for model in models_sorted:
            if (level, model) not in seen:
                problems.append(
                    f"The grid is incomplete: no condition runs {level} on {model}."
                )
    return problems


def _rate(outcomes: Outcomes, cases: Sequence[str]) -> float | None:
    """Passed runs over scored runs, pooled across ``cases``.

    Pooled, like ``success_delta`` and the Newcombe floor: one estimand for the
    point, its interval and the passed/runs counts printed beside it. A case
    with more repetitions weighs more, which is what its extra runs bought.
    """
    runs = sum(len(outcomes[case]) for case in cases if outcomes.get(case))
    passed = sum(
        sum(bool(v) for v in outcomes[case]) for case in cases if outcomes.get(case)
    )
    return passed / runs if runs else None


def _resampled_rate(
    rng: random.Random, outcomes: Outcomes, drawn: Sequence[str]
) -> float | None:
    """The pooled rate over a resample: drawn cases, runs redrawn within each."""
    total_runs = total_passes = 0
    for case in drawn:
        runs = outcomes.get(case)
        if not runs:
            continue
        passes = sum(bool(v) for v in runs)
        if 0 < passes < len(runs):
            rate = passes / len(runs)
            passes = sum(1 for _ in range(len(runs)) if rng.random() < rate)
        total_runs += len(runs)
        total_passes += passes
    return total_passes / total_runs if total_runs else None


def _quantile(ordered: Sequence[float], q: float) -> float:
    position = (len(ordered) - 1) * q
    below = int(position)
    above = min(below + 1, len(ordered) - 1)
    return ordered[below] + (ordered[above] - ordered[below]) * (position - below)


def analyze(
    grid: Mapping[Cell, Outcomes],
    *,
    harness_levels: Sequence[str],
    models: Sequence[str],
    costs: Mapping[Cell, Mapping[str, Sequence[float | None]]] | None = None,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Effects, interaction and per-cell figures for a full grid.

    ``harness_levels`` is ordered from least to most harness: every effect is
    measured against the first. ``models`` is ordered as the reader declares
    (typically smallest to largest): the interaction runs first → last.

    ``costs`` gives, per cell and case, the cost of each scored run (None for a
    run that reported none). A cell is priced only over the paired cases, and
    only when every one of those runs has a cost: a missing figure is not $0.
    """
    base, top = harness_levels[0], harness_levels[-1]
    first, last = models[0], models[-1]
    # Only cases every cell scored take part, so all comparisons are paired.
    cases = sorted(
        set.intersection(
            *(
                {case for case, runs in grid.get(cell, {}).items() if runs}
                for cell in ((lv, m) for lv in harness_levels for m in models)
            )
        )
    )

    def effects_for(rate_of) -> dict[tuple[str, str], float | None]:
        out: dict[tuple[str, str], float | None] = {}
        for model in models:
            base_rate = rate_of((base, model))
            for level in harness_levels[1:]:
                rate = rate_of((level, model))
                out[(model, level)] = (
                    None
                    if rate is None or base_rate is None
                    else (rate - base_rate) * 100
                )
        return out

    point_effects = effects_for(lambda cell: _rate(grid[cell], cases))

    def interaction_of(effects: Mapping[tuple[str, str], float | None]) -> float | None:
        a, b = effects.get((first, top)), effects.get((last, top))
        return None if a is None or b is None else b - a

    point_interaction = interaction_of(point_effects)

    rng = random.Random(seed)
    boot_effects: dict[tuple[str, str], list[float]] = {k: [] for k in point_effects}
    boot_interaction: list[float] = []
    if cases:
        for _ in range(resamples):
            drawn = rng.choices(cases, k=len(cases))
            rates = {cell: _resampled_rate(rng, grid[cell], drawn) for cell in grid}
            sample = effects_for(lambda cell, rates=rates: rates.get(cell))
            for key, value in sample.items():
                if value is not None:
                    boot_effects[key].append(value)
            inter = interaction_of(sample)
            if inter is not None:
                boot_interaction.append(inter)

    def interval(values: list[float]) -> tuple[float | None, float | None]:
        if not values:
            return (None, None)
        ordered = sorted(values)
        return (_quantile(ordered, 0.025), _quantile(ordered, 0.975))

    def counts(cell: Cell) -> tuple[int, int]:
        outcomes = grid.get(cell, {})
        runs = sum(len(outcomes[c]) for c in cases if outcomes.get(c))
        passed = sum(
            sum(bool(v) for v in outcomes[c]) for c in cases if outcomes.get(c)
        )
        return passed, runs

    # Newcombe's interval per effect, in points: the floor the bootstrap may not
    # narrow below. It does not collapse on a unanimous cell (ADR-0064), which
    # is the one place a resampling interval does.
    newcombe: dict[tuple[str, str], tuple[float, float]] = {}
    for model in models:
        base_passed, base_runs = counts((base, model))
        for level in harness_levels[1:]:
            passed, runs = counts((level, model))
            if base_runs and runs:
                n_lo, n_hi = _newcombe_interval(base_passed, base_runs, passed, runs)
                newcombe[(model, level)] = (n_lo * 100, n_hi * 100)

    effect_rows = []
    for (model, level), points in point_effects.items():
        low, high = interval(boot_effects[(model, level)])
        floor = newcombe.get((model, level))
        if floor is not None:
            low = floor[0] if low is None else min(low, floor[0])
            high = floor[1] if high is None else max(high, floor[1])
        if points is not None and low is not None and high is not None:
            low, high = min(low, points), max(high, points)
        effect_rows.append(
            {
                "model": model,
                "level": level,
                "points": points,
                "ci_low": low,
                "ci_high": high,
            }
        )

    i_low, i_high = interval(boot_interaction)
    if point_interaction is not None:
        # The difference of two independent effects: half-widths add in
        # quadrature, per side.
        a_floor, b_floor = newcombe.get((first, top)), newcombe.get((last, top))
        a_point, b_point = (
            point_effects.get((first, top)),
            point_effects.get((last, top)),
        )
        if a_floor and b_floor and a_point is not None and b_point is not None:
            below = ((b_point - b_floor[0]) ** 2 + (a_floor[1] - a_point) ** 2) ** 0.5
            above = ((b_floor[1] - b_point) ** 2 + (a_point - a_floor[0]) ** 2) ** 0.5
            n_low, n_high = point_interaction - below, point_interaction + above
            i_low = n_low if i_low is None else min(i_low, n_low)
            i_high = n_high if i_high is None else max(i_high, n_high)
        if i_low is not None and i_high is not None:
            i_low, i_high = (
                min(i_low, point_interaction),
                max(i_high, point_interaction),
            )
            # A difference of two differences of rates lives in [-200, 200] pp.
            i_low, i_high = max(-200.0, i_low), min(200.0, i_high)
    established = (
        len(cases) >= MIN_CASES_TO_ESTABLISH
        and i_low is not None
        and i_high is not None
        and (i_low > 0 or i_high < 0)
    )

    cell_rows = []
    for level in harness_levels:
        for model in models:
            outcomes = grid.get((level, model), {})
            runs = sum(len(outcomes[c]) for c in cases if outcomes.get(c))
            passed = sum(
                sum(bool(v) for v in outcomes[c]) for c in cases if outcomes.get(c)
            )
            per_run = [
                value
                for case in cases
                for value in ((costs or {}).get((level, model), {}).get(case) or [])
            ]
            cost = (
                sum(value for value in per_run if value is not None)
                if per_run and len(per_run) == runs and None not in per_run
                else None
            )
            cell_rows.append(
                {
                    "level": level,
                    "model": model,
                    "runs": runs,
                    "passed": passed,
                    "pass_rate": _rate(outcomes, cases),
                    "cost_usd": cost,
                    "cost_per_resolved_usd": (cost / passed)
                    if cost is not None and passed
                    else None,
                }
            )

    interaction = {
        "level": top,
        "from_model": first,
        "to_model": last,
        "points": point_interaction,
        "ci_low": i_low,
        "ci_high": i_high,
        "established": established,
    }
    return {
        "harness_levels": list(harness_levels),
        "models": list(models),
        "paired_cases": len(cases),
        "cells": cell_rows,
        "harness_effect": effect_rows,
        "interaction": interaction,
        "reading": _reading(point_effects, interaction, top, len(cases)),
    }


def _fmt(points: float | None) -> str:
    if points is None:
        return "n/a"
    rounded = round(points)
    # Never "-0" or "+0" beside a claim about zero: a value that rounds to zero
    # but is not zero keeps its sign and one decimal.
    if rounded == 0 and points != 0:
        return f"{points:+.1f} pp"
    return f"{rounded:+d} pp" if rounded else "0 pp"


def _reading(
    effects: Mapping[tuple[str, str], float | None],
    interaction: Mapping[str, Any],
    top: str,
    cases: int,
) -> str:
    first, last = interaction["from_model"], interaction["to_model"]
    if interaction["points"] is None:
        return (
            "No case was scored in every cell, so the harness effect cannot be "
            "compared across models yet."
        )
    a, b = effects.get((first, top)), effects.get((last, top))
    # The size of the effect, not the sign of the change: a harness that harms
    # less on the stronger model has a positive interaction and a shrinking
    # effect, and one that flips from +20 to -20 is not "shrinking".
    if a is None or b is None or abs(a) == abs(b):
        direction = "stays the same size"
    elif (a > 0) != (b > 0) and a != 0 and b != 0:
        direction = "changes sign"
    elif abs(b) < abs(a):
        direction = "shrinks"
    else:
        direction = "grows"
    span = f"[{_fmt(interaction['ci_low'])}, {_fmt(interaction['ci_high'])}]"
    head = (
        f"On {cases} paired case(s), the '{top}' harness effect {direction} from "
        f"{_fmt(a)} on {first} to {_fmt(b)} on {last}"
    )
    if interaction["established"]:
        return (
            f"{head}; the interval of the difference, {span}, excludes zero, so on this "
            "corpus the change is established. It holds for these cases and models only."
        )
    if cases < MIN_CASES_TO_ESTABLISH:
        return (
            f"{head}. With fewer than {MIN_CASES_TO_ESTABLISH} paired cases a change "
            "across models is not established, whatever the interval "
            f"({span}): the cases are the unit of evidence, not the runs."
        )
    # Not "more cases narrow it faster" unconditionally: a Monte Carlo check
    # (Trinidad, 2026-11-06) found 4 cases x 12 and 16 x 3 equally wide
    # (69 vs 68 points) when the effect was even across cases. Cases win when
    # the effect varies from case to case, which is the common situation and
    # the one repetitions cannot average away — so that is the condition said.
    return (
        f"{head}, but the interval of the difference, {span}, includes zero: a "
        "change across models is not established. When the harness helps some "
        "cases and not others, more cases narrow it faster than more repetitions; "
        "when it moves every case alike, either does."
    )
