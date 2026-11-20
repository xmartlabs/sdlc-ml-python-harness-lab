"""How much harness does a model need? A ladder of clean A/Bs (ADR-0093).

The guided Harness flow lets a person pick two or three of none/minimal/full on
one case and one model. One three-arm experiment would move two harness factors
at once; the ladder makes one clean A/B per adjacent pair, in one family.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import select

from harness_lab_app.experiment_service import validate_design
from harness_lab_app.harness_levels import ladder_designs, rungs
from harness_lab_app.models import Case, CaseType, ExperimentCondition, Run
from harness_lab_app.schemas import HarnessLevelsCreate
from harness_lab_app.skills_catalog import profile_disabled


def _endpoint(path: str, method: str):
    from harness_lab_app.api import create_app

    for route in create_app().router.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


@pytest.mark.parametrize(
    ("levels", "expected"),
    [
        (["bare", "minimal", "full"], [("bare", "minimal"), ("minimal", "full")]),
        (["full", "bare"], [("bare", "full")]),
        (["minimal", "full"], [("minimal", "full")]),
    ],
)
def test_rungs_are_the_adjacent_pairs_in_ladder_order(levels, expected):
    assert rungs(levels) == expected


def test_one_level_is_not_a_comparison():
    with pytest.raises(ValueError, match="at least two"):
        rungs(["full"])


def test_every_rung_is_a_clean_single_factor_ab():
    designs = ladder_designs(
        case_id="chess3d-v1",
        model="claude-haiku-4-5",
        harness="claude",
        levels=["bare", "minimal", "full"],
        reps=1,
        minimal_disabled=profile_disabled("minimal"),
    )
    assert len({design.family_id for design in designs}) == 1
    for design in designs:
        conditions = [
            {"label": c.label, "role": c.role, "overrides": c.overrides}
            for c in design.conditions
        ]
        warnings = validate_design(design.mode, conditions, in_family=True)
        assert not any("More than one harness factor" in w for w in warnings), warnings
        for condition in design.conditions:
            assert condition.overrides["executor_model"] == "claude-haiku-4-5"
            assert condition.overrides["prompt_variant"] == "default"


def test_the_endpoint_creates_the_rungs_and_never_launches(session):
    session.add(
        Case(
            case_id="chess3d-v1",
            name="chess3d-v1",
            type=CaseType.build_from_scratch,
            prompt_variants={"default": "d", "harness": "h"},
        )
    )
    session.commit()
    created = _endpoint("/api/experiments/harness-levels", "POST")(
        data=HarnessLevelsCreate(
            case_id="chess3d-v1",
            model="claude-haiku-4-5",
            levels=["bare", "minimal", "full"],
            repetitions=1,
        ),
        session=session,
    )
    ids = [row["experiment_id"] for row in created["experiments"]]
    assert len(ids) == 2
    labels = sorted(
        c.label
        for c in session.exec(
            select(ExperimentCondition).where(
                ExperimentCondition.experiment_id.in_(ids)
            )  # type: ignore[attr-defined]
        ).all()
    )
    assert labels == ["bare", "full", "minimal", "minimal"]
    assert session.exec(select(Run)).all() == []


def test_an_effort_is_pinned_in_every_arm_of_every_rung(session):
    """`--effort` is part of the arm (ADR-0081): set once, held in every level,
    so the only thing that moves within a rung is still the harness level."""
    session.add(
        Case(
            case_id="chess3d-v1",
            name="chess3d-v1",
            type=CaseType.build_from_scratch,
            prompt_variants={"default": "d", "harness": "h"},
        )
    )
    session.commit()
    created = _endpoint("/api/experiments/harness-levels", "POST")(
        data=HarnessLevelsCreate(
            case_id="chess3d-v1",
            model="claude-opus-5-5",
            levels=["bare", "full"],
            effort="high",
        ),
        session=session,
    )
    ids = [row["experiment_id"] for row in created["experiments"]]
    conditions = session.exec(
        select(ExperimentCondition).where(ExperimentCondition.experiment_id.in_(ids))  # type: ignore[attr-defined]
    ).all()
    assert {c.overrides.get("executor_effort") for c in conditions} == {"high"}


def test_no_effort_leaves_the_arms_exactly_as_before():
    for design in ladder_designs(
        case_id="tetris-v2",
        model="claude-haiku-4-5",
        harness="claude",
        levels=["minimal", "full"],
        reps=1,
        minimal_disabled=["a"],
    ):
        for condition in design.conditions:
            assert "executor_effort" not in condition.overrides


def test_a_case_without_a_plain_prompt_is_refused(session):
    session.add(
        Case(
            case_id="only-harness",
            name="only-harness",
            type=CaseType.build_from_scratch,
            prompt_variants={"harness": "h"},
        )
    )
    session.commit()
    with pytest.raises(HTTPException) as exc:
        _endpoint("/api/experiments/harness-levels", "POST")(
            data=HarnessLevelsCreate(
                case_id="only-harness", model="m", levels=["bare", "full"]
            ),
            session=session,
        )
    assert exc.value.status_code == 422
