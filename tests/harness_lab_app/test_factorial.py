"""Harness x model analysis (ADR-0066): does the harness help less on stronger models?"""

from __future__ import annotations

import pytest

from harness_lab_app.factorial import analyze, grid_problems, harness_axis, level_of


def _cell(passes: int, runs: int) -> list[bool]:
    return [True] * passes + [False] * (runs - passes)


# Two cases, two harness levels, two models.
#   small model: bare 1/4 per case → harness 3/4 per case  (+50 pp)
#   large model: bare 3/4 per case → harness 4/4 per case  (+25 pp)
SHRINKING = {
    ("bare", "haiku"): {"a": _cell(1, 4), "b": _cell(1, 4)},
    ("full", "haiku"): {"a": _cell(3, 4), "b": _cell(3, 4)},
    ("bare", "opus"): {"a": _cell(3, 4), "b": _cell(3, 4)},
    ("full", "opus"): {"a": _cell(4, 4), "b": _cell(4, 4)},
}


def test_the_harness_effect_is_measured_per_model():
    result = analyze(
        SHRINKING, harness_levels=["bare", "full"], models=["haiku", "opus"]
    )
    effects = {row["model"]: row for row in result["harness_effect"]}
    assert effects["haiku"]["points"] == pytest.approx(50.0)
    assert effects["opus"]["points"] == pytest.approx(25.0)


def test_the_interaction_is_the_change_in_effect_from_the_first_to_the_last_model():
    result = analyze(
        SHRINKING, harness_levels=["bare", "full"], models=["haiku", "opus"]
    )
    interaction = result["interaction"]
    assert interaction["points"] == pytest.approx(-25.0)
    assert interaction["from_model"] == "haiku"
    assert interaction["to_model"] == "opus"
    low, high = interaction["ci_low"], interaction["ci_high"]
    assert low <= -25.0 <= high


def test_every_effect_carries_an_interval_that_contains_it():
    result = analyze(
        SHRINKING, harness_levels=["bare", "full"], models=["haiku", "opus"]
    )
    for row in result["harness_effect"]:
        assert row["ci_low"] <= row["points"] <= row["ci_high"]


def test_two_cases_cannot_establish_an_interaction():
    """The reading must never outrun the interval."""
    result = analyze(
        SHRINKING, harness_levels=["bare", "full"], models=["haiku", "opus"]
    )
    assert result["interaction"]["established"] is False
    assert "not established" in result["reading"]


def test_a_large_consistent_interaction_is_established():
    cases = {f"c{i}": None for i in range(20)}
    grid = {
        ("bare", "haiku"): {c: _cell(0, 5) for c in cases},
        ("full", "haiku"): {c: _cell(5, 5) for c in cases},
        ("bare", "opus"): {c: _cell(5, 5) for c in cases},
        ("full", "opus"): {c: _cell(5, 5) for c in cases},
    }
    result = analyze(grid, harness_levels=["bare", "full"], models=["haiku", "opus"])
    assert result["interaction"]["points"] == pytest.approx(-100.0)
    assert result["interaction"]["established"] is True
    assert "shrinks" in result["reading"]


def test_unanimous_cells_on_few_cases_never_collapse_to_certainty():
    """0/3 vs 3/3 on one case resamples to the same draw every time; an interval
    built only from those resamples would read [-100, -100] — certainty from
    twelve runs. The interval must stay wide and nothing may be established."""
    grid = {
        ("bare", "s"): {"a": _cell(0, 3)},
        ("full", "s"): {"a": _cell(3, 3)},
        ("bare", "l"): {"a": _cell(3, 3)},
        ("full", "l"): {"a": _cell(3, 3)},
    }
    result = analyze(grid, harness_levels=["bare", "full"], models=["s", "l"])
    interaction = result["interaction"]
    assert interaction["established"] is False
    assert interaction["ci_high"] - interaction["ci_low"] > 50
    for row in result["harness_effect"]:
        assert row["ci_high"] - row["ci_low"] > 30


def test_rate_counts_effect_and_interval_describe_one_estimate():
    """Unequal repetitions per case (case_repetitions, unscored runs) used to give
    a case-weighted point next to pooled counts and a pooled interval — 33% shown
    beside 1/21. Every figure now pools runs, as success_delta does."""
    grid = {
        ("bare", "h"): {"c1": _cell(0, 10), "c2": _cell(1, 1), "c3": _cell(0, 10)},
        ("full", "h"): {"c1": _cell(3, 10), "c2": _cell(0, 1), "c3": _cell(0, 10)},
        ("bare", "o"): {"c1": _cell(0, 10), "c2": _cell(0, 1), "c3": _cell(0, 10)},
        ("full", "o"): {"c1": _cell(0, 10), "c2": _cell(0, 1), "c3": _cell(0, 10)},
    }
    result = analyze(grid, harness_levels=["bare", "full"], models=["h", "o"])
    for cell in result["cells"]:
        assert cell["pass_rate"] == pytest.approx(cell["passed"] / cell["runs"])
    effect = next(r for r in result["harness_effect"] if r["model"] == "h")
    assert effect["points"] == pytest.approx((3 / 21 - 1 / 21) * 100)
    assert effect["ci_low"] <= effect["points"] <= effect["ci_high"]


def test_the_reading_describes_the_size_of_the_effect_not_the_sign_of_the_change():
    """A harness that harms less on the stronger model has a positive interaction
    but a *shrinking* effect; 'grows' would invert the finding."""
    grid = {
        ("bare", "s"): {f"c{i}": _cell(5, 5) for i in range(20)},
        ("full", "s"): {f"c{i}": _cell(0, 5) for i in range(20)},
        ("bare", "l"): {f"c{i}": _cell(5, 5) for i in range(20)},
        ("full", "l"): {f"c{i}": _cell(4, 5) for i in range(20)},
    }
    result = analyze(grid, harness_levels=["bare", "full"], models=["s", "l"])
    assert result["interaction"]["points"] > 0
    assert "shrinks" in result["reading"]


def test_an_open_interaction_says_when_more_cases_beat_more_repetitions():
    """Cases narrow the interval faster only when the effect varies by case.

    The reading used to say so unconditionally; a Monte Carlo check found
    4 cases x 12 and 16 x 3 equally wide under an effect even across cases.
    """
    cases = [f"c{i}" for i in range(4)]
    grid = {
        ("bare", "s"): {c: _cell(2, 5) for c in cases},
        ("full", "s"): {c: _cell(3, 5) for c in cases},
        ("bare", "l"): {c: _cell(2, 5) for c in cases},
        ("full", "l"): {c: _cell(3, 5) for c in cases},
    }
    result = analyze(grid, harness_levels=["bare", "full"], models=["s", "l"])
    assert result["interaction"]["established"] is False
    reading = result["reading"]
    assert "helps some cases and not others" in reading
    assert "either does" in reading


def test_the_reading_never_says_significant_or_proves():
    result = analyze(
        SHRINKING, harness_levels=["bare", "full"], models=["haiku", "opus"]
    )
    text = result["reading"].lower()
    for banned in ("significant", "prove", "proof", "causes"):
        assert banned not in text


def test_three_harness_levels_report_each_against_the_first():
    grid = {
        (level, model): {"a": _cell(p, 4)}
        for (level, model, p) in [
            ("bare", "m", 1),
            ("short", "m", 2),
            ("full", "m", 3),
            ("bare", "n", 2),
            ("short", "n", 3),
            ("full", "n", 4),
        ]
    }
    result = analyze(grid, harness_levels=["bare", "short", "full"], models=["m", "n"])
    levels = {(row["model"], row["level"]) for row in result["harness_effect"]}
    assert levels == {("m", "short"), ("m", "full"), ("n", "short"), ("n", "full")}


def _per_run(total: float, runs_per_case: int = 4) -> dict[str, list[float]]:
    """Spread a cell's total cost evenly over its runs on cases a and b."""
    each = total / (2 * runs_per_case)
    return {"a": [each] * runs_per_case, "b": [each] * runs_per_case}


def test_cost_per_resolved_task_is_reported_per_cell():
    costs = {
        ("bare", "haiku"): _per_run(2.0),
        ("full", "haiku"): _per_run(3.0),
        ("bare", "opus"): _per_run(8.0),
        ("full", "opus"): _per_run(9.0),
    }
    result = analyze(
        SHRINKING,
        harness_levels=["bare", "full"],
        models=["haiku", "opus"],
        costs=costs,
    )
    cell = next(
        c for c in result["cells"] if c["level"] == "full" and c["model"] == "haiku"
    )
    # 6 of 8 runs passed, $3.00 total → $0.50 per resolved task
    assert cell["passed"] == 6 and cell["runs"] == 8
    assert cell["cost_per_resolved_usd"] == pytest.approx(0.5)


def test_a_cell_with_no_pass_has_no_cost_per_resolved():
    grid = dict(SHRINKING)
    grid[("bare", "haiku")] = {"a": _cell(0, 4), "b": _cell(0, 4)}
    result = analyze(
        grid,
        harness_levels=["bare", "full"],
        models=["haiku", "opus"],
        costs={("bare", "haiku"): _per_run(1.0)},
    )
    cell = next(
        c for c in result["cells"] if c["level"] == "bare" and c["model"] == "haiku"
    )
    assert cell["cost_per_resolved_usd"] is None


def test_a_run_without_a_cost_leaves_the_cell_unpriced():
    """A missing figure is not $0: pricing the runs that have one would make the
    cell look cheaper than it was."""
    costs = {("full", "haiku"): {"a": [1.0, 1.0, None, 1.0], "b": [1.0] * 4}}
    result = analyze(
        SHRINKING,
        harness_levels=["bare", "full"],
        models=["haiku", "opus"],
        costs=costs,
    )
    cell = next(
        c for c in result["cells"] if (c["level"], c["model"]) == ("full", "haiku")
    )
    assert cell["cost_usd"] is None and cell["cost_per_resolved_usd"] is None


def test_an_unpaired_case_is_not_charged_to_the_paired_passes():
    grid = dict(SHRINKING)
    grid[("full", "haiku")] = {**SHRINKING[("full", "haiku")], "c9": _cell(4, 4)}
    costs = {("full", "haiku"): {**_per_run(4.0), "c9": [100.0] * 4}}
    result = analyze(
        grid, harness_levels=["bare", "full"], models=["haiku", "opus"], costs=costs
    )
    cell = next(
        c for c in result["cells"] if (c["level"], c["model"]) == ("full", "haiku")
    )
    assert cell["runs"] == 8
    assert cell["cost_usd"] == pytest.approx(4.0)


def test_the_analysis_is_deterministic():
    one = analyze(SHRINKING, harness_levels=["bare", "full"], models=["haiku", "opus"])
    two = analyze(SHRINKING, harness_levels=["bare", "full"], models=["haiku", "opus"])
    assert one == two


# --- design: the grid ----------------------------------------------------------


def _cond(label, model, **harness):
    return {"label": label, "overrides": {"executor_model": model, **harness}}


def test_harness_axis_names_the_one_varying_harness_field():
    conditions = [
        _cond("a", "haiku", harness_source="bare"),
        _cond("b", "haiku", harness_source="lab_root"),
        _cond("c", "opus", harness_source="bare"),
        _cond("d", "opus", harness_source="lab_root"),
    ]
    assert harness_axis(conditions) == "harness_source"


def test_a_complete_grid_has_no_problems():
    conditions = [
        _cond("a", "haiku", harness_source="bare"),
        _cond("b", "haiku", harness_source="lab_root"),
        _cond("c", "opus", harness_source="bare"),
        _cond("d", "opus", harness_source="lab_root"),
    ]
    assert grid_problems(conditions) == []


def test_a_missing_cell_is_named():
    conditions = [
        _cond("a", "haiku", harness_source="bare"),
        _cond("b", "haiku", harness_source="lab_root"),
        _cond("c", "opus", harness_source="bare"),
    ]
    problems = grid_problems(conditions)
    assert any("opus" in p and "lab_root" in p for p in problems)


def test_one_model_is_not_a_grid():
    conditions = [
        _cond("a", "haiku", harness_source="bare"),
        _cond("b", "haiku", harness_source="lab_root"),
    ]
    assert any("two models" in p for p in grid_problems(conditions))


def _ladder(models=("small", "large")):
    """none → governance only (skills off) → full harness, on every model."""
    conditions = []
    for model in models:
        conditions += [
            _cond(f"none-{model}", model, harness_source="bare"),
            _cond(
                f"gov-{model}",
                model,
                harness_source="lab_root",
                skills_disabled=["a", "b"],
            ),
            _cond(f"full-{model}", model, harness_source="lab_root"),
        ]
    return conditions


def test_a_nested_harness_ladder_is_a_valid_grid():
    """Partial vs full harness: each level adds to the one before (ADR-0051), so
    the levels are ordered even though two harness fields move."""
    assert grid_problems(_ladder()) == []


def test_ladder_levels_are_named_after_the_harness_they_describe():
    assert harness_axis(_ladder()) == "harness_level"


def test_two_harness_axes_varying_is_refused():
    conditions = [
        _cond("a", "haiku", harness_source="bare", prompt_variant="default"),
        _cond("b", "haiku", harness_source="lab_root", prompt_variant="harness"),
        _cond("c", "opus", harness_source="bare", prompt_variant="default"),
        _cond("d", "opus", harness_source="lab_root", prompt_variant="harness"),
    ]
    assert any("one harness" in p for p in grid_problems(conditions))


def test_a_duplicate_cell_is_refused():
    conditions = [
        _cond("a", "haiku", harness_source="bare"),
        _cond("a2", "haiku", harness_source="bare"),
        _cond("b", "haiku", harness_source="lab_root"),
        _cond("c", "opus", harness_source="bare"),
        _cond("d", "opus", harness_source="lab_root"),
    ]
    assert any("twice" in p for p in grid_problems(conditions))


def test_ladder_levels_get_short_readable_labels():
    """A level that disables thirty skills must not print thirty names."""
    conditions = _ladder()
    long = [
        _cond(
            c["label"],
            c["overrides"]["executor_model"],
            **{
                k: (["s"] * 30 if k == "skills_disabled" else v)
                for k, v in c["overrides"].items()
                if k != "executor_model"
            },
        )
        for c in conditions
    ]
    labels = {level_of(c, long) for c in long}
    assert all(len(label) < 60 for label in labels), labels
    assert any("skills off" in label for label in labels)
