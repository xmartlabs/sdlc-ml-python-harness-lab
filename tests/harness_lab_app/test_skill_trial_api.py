"""The web UI can create an additive skill trial (ADR-0089, ADR-0090).

The button on the Skills page calls ``POST /api/experiments/skill-trial``. It
must build the same experiment the CLI builds (``make add-skill-trial``), and it
must only *create* it: launching stays on the Experiments page, behind the same
launch form and preflight as every other experiment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlmodel import select

from harness_lab_app.models import (
    Case,
    CaseType,
    Experiment,
    ExperimentCondition,
    ExperimentMode,
    Run,
)
from harness_lab_app.schemas import SkillTrialCreate
from harness_lab_app.skill_trial import design, trial_experiment_id
from harness_lab_app.skills_catalog import profile_disabled, skill_profiles

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _endpoint(path: str, method: str):
    from harness_lab_app.api import create_app

    for route in create_app().router.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


def _outside_minimal() -> str:
    """A governed skill the minimal profile hides — the kind a trial adds."""
    return sorted(profile_disabled("minimal"))[0]


def _seed_cases(session, *case_ids: str) -> None:
    for case_id in case_ids:
        session.add(
            Case(
                case_id=case_id,
                name=case_id,
                type=CaseType.build_from_scratch,
                prompt_variants={"default": "do it", "harness": "do it, governed"},
            )
        )
    session.commit()


@pytest.fixture
def create_trial():
    return _endpoint("/api/experiments/skill-trial", "POST")


def test_the_web_trial_builds_the_same_arms_as_the_cli(session, create_trial):
    skill = _outside_minimal()
    _seed_cases(session, "a-case", "b-case", "c-case")
    payload = SkillTrialCreate(
        skill=skill, model="claude-haiku-4-5", case_ids=["a-case", "b-case", "c-case"]
    )

    created = create_trial(data=payload, session=session)

    expected = design(
        skill=skill,
        cases=["a-case", "b-case", "c-case"],
        model="claude-haiku-4-5",
        reps=3,
        base_disabled=profile_disabled("minimal"),
    )
    stored = session.exec(
        select(Experiment).where(Experiment.experiment_id == created["experiment_id"])
    ).one()
    assert stored.mode == ExperimentMode.harness_evaluation
    assert stored.family_id == expected.family_id
    assert stored.repetitions == 3
    arms = {
        c.role: c.overrides
        for c in session.exec(
            select(ExperimentCondition).where(
                ExperimentCondition.experiment_id == stored.experiment_id
            )
        ).all()
    }
    # The CLI's arms, plus the model and harness the web pins in both (the CLI
    # passes those at launch instead).
    pinned = {"executor_model": "claude-haiku-4-5", "harness": "claude"}
    expected_arms = {c.role: {**pinned, **c.overrides} for c in expected.conditions}
    assert arms == expected_arms
    # The treatment is the base profile plus exactly this one skill, asked for.
    assert arms["treatment"]["skills_invoke"] == [skill]
    assert set(arms["control"]["skills_disabled"]) - set(
        arms["treatment"]["skills_disabled"]
    ) == {skill}


def test_the_web_trial_creates_but_never_launches(session, create_trial):
    _seed_cases(session, "a-case", "b-case", "c-case")
    create_trial(
        data=SkillTrialCreate(
            skill=_outside_minimal(), model="m", case_ids=["a-case", "b-case", "c-case"]
        ),
        session=session,
    )
    assert session.exec(select(Run)).all() == []


def test_a_second_trial_of_the_same_skill_gets_its_own_id(session, create_trial):
    skill = _outside_minimal()
    _seed_cases(session, "a-case", "b-case", "c-case")
    payload = SkillTrialCreate(
        skill=skill, model="m", case_ids=["a-case", "b-case", "c-case"]
    )

    first = create_trial(data=payload, session=session)
    second = create_trial(data=payload, session=session)

    assert first["experiment_id"] == trial_experiment_id(skill, "minimal", "m")
    assert second["experiment_id"] != first["experiment_id"]
    assert second["experiment_id"].startswith(first["experiment_id"][:60])


def test_adding_a_skill_minimal_already_carries_is_a_422(session, create_trial):
    carried = skill_profiles()["minimal"]["skills"][0]
    _seed_cases(session, "a-case")
    with pytest.raises(HTTPException) as exc:
        create_trial(
            data=SkillTrialCreate(skill=carried, model="m", case_ids=["a-case"]),
            session=session,
        )
    assert exc.value.status_code == 422
    assert "already in profile" in exc.value.detail


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("skill", "not-a-skill", "not a governed skill"),
        ("profile", "tiny", "Unknown skill profile"),
    ],
)
def test_an_unknown_skill_or_profile_is_a_422(
    session, create_trial, field, value, message
):
    _seed_cases(session, "a-case")
    data = {"skill": _outside_minimal(), "model": "m", "case_ids": ["a-case"]}
    data[field] = value
    with pytest.raises(HTTPException) as exc:
        create_trial(data=SkillTrialCreate(**data), session=session)
    assert exc.value.status_code == 422
    assert message in exc.value.detail


def test_the_cli_still_designs_through_the_shared_module():
    import add_skill_trial

    assert add_skill_trial.design is design


def test_both_web_arms_pin_the_same_model_and_harness(session, create_trial):
    """The experiment launches from a shared form with its own defaults.

    An arm that did not name its model would run whatever that form held, so
    the trial would measure a different model than its name says. Both arms
    pin the same values, so the design still varies one harness factor.
    """
    _seed_cases(session, "a-case", "b-case", "c-case")
    created = create_trial(
        data=SkillTrialCreate(
            skill=_outside_minimal(),
            model="claude-haiku-4-5",
            harness="claude",
            case_ids=["a-case", "b-case", "c-case"],
        ),
        session=session,
    )
    arms = [
        c.overrides
        for c in session.exec(
            select(ExperimentCondition).where(
                ExperimentCondition.experiment_id == created["experiment_id"]
            )
        ).all()
    ]
    assert len(arms) == 2
    assert {arm["executor_model"] for arm in arms} == {"claude-haiku-4-5"}
    assert {arm["harness"] for arm in arms} == {"claude"}
    assert not any("More than one harness factor" in w for w in created["warnings"])


def test_the_cli_design_does_not_pin_the_model_in_its_arms():
    """The CLI passes model and harness at launch; its arms stay as they were."""
    trial = design(
        skill="x",
        cases=["a"],
        model="m",
        reps=1,
        base_disabled=["x"],
    )
    for condition in trial.conditions:
        assert "executor_model" not in condition.overrides
        assert "harness" not in condition.overrides


def test_a_model_id_with_a_colon_makes_a_valid_experiment_id(session, create_trial):
    """Ollama names models `qwen3:8b`; the id used to fail the slug check."""
    _seed_cases(session, "a-case")
    created = create_trial(
        data=SkillTrialCreate(
            skill=_outside_minimal(), model="qwen3:8b", case_ids=["a-case"]
        ),
        session=session,
    )
    assert created["experiment_id"].endswith("-qwen3-8b")


def test_an_empty_model_is_refused_before_anything_is_built():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SkillTrialCreate(skill="x", model="", case_ids=["a-case"])
