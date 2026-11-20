"""A skill is trialled against a chosen base: minimal, full, or both (ADR-0092).

"Does this skill belong in the template?" is two questions: does it help on top
of ``minimal`` (what every new project carries) and on top of ``full``? Each
base is one clean A/B — the base against the base plus the skill, asked for —
and ``both`` creates the two A/Bs in one family so they are read side by side,
never a four-arm design that varies two harness factors at once.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select

from harness_lab_app import staged_skill_service as service_module
from harness_lab_app.models import Case, CaseType, ExperimentCondition, Run
from harness_lab_app.schemas import SkillTrialCreate
from harness_lab_app.skill_trial import base_design
from harness_lab_app.skills_catalog import profile_disabled


def _endpoint(path: str, method: str):
    from harness_lab_app.api import create_app

    for route in create_app().router.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


@pytest.fixture
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(
        service_module, "get_settings", lambda: SimpleNamespace(repo_root=tmp_path)
    )
    monkeypatch.setattr(service_module, "known_skill_names", lambda: set())
    return tmp_path


def _seed(session, case_id: str = "pr-review-v1") -> None:
    session.add(
        Case(
            case_id=case_id,
            name=case_id,
            type=CaseType.bugfix,
            prompt_variants={"default": "d", "harness": "h"},
        )
    )
    session.commit()


def _arms(session, experiment_id: str) -> dict[str, dict]:
    return {
        c.role: c.overrides
        for c in session.exec(
            select(ExperimentCondition).where(
                ExperimentCondition.experiment_id == experiment_id
            )
        ).all()
    }


def test_a_staged_skill_on_minimal_adds_it_to_the_minimal_profile():
    minimal = profile_disabled("minimal")
    trial = base_design(
        skill="review_pull_request",
        staged=True,
        base="minimal",
        cases=["pr-review-v1"],
        model="m",
        reps=5,
    )
    control, treatment = (c.overrides for c in trial.conditions)
    assert control["skills_disabled"] == minimal
    assert treatment["skills_disabled"] == minimal
    assert control["skills_extra"] == []
    assert treatment["skills_extra"] == ["review_pull_request"]
    assert treatment["skills_invoke"] == ["review_pull_request"]


def test_a_staged_skill_on_full_disables_nothing():
    trial = base_design(
        skill="review_pull_request",
        staged=True,
        base="full",
        cases=["pr-review-v1"],
        model="m",
        reps=5,
    )
    control, treatment = (c.overrides for c in trial.conditions)
    assert control["skills_disabled"] == []
    assert treatment["skills_disabled"] == []
    assert treatment["skills_extra"] == ["review_pull_request"]


def test_a_catalog_skill_on_full_is_refused_because_full_already_carries_it():
    outside_minimal = sorted(profile_disabled("minimal"))[0]
    with pytest.raises(ValueError, match="already"):
        base_design(
            skill=outside_minimal,
            staged=False,
            base="full",
            cases=["a"],
            model="m",
            reps=1,
        )


def test_both_bases_create_two_clean_ab_in_one_family(session, staging):
    _seed(session)
    _endpoint("/api/cases/{case_id}/candidate-skills/{name}/stage", "POST")(
        case_id="pr-review-v1",
        name="review_pull_request",
        replace=False,
        session=session,
    )
    created = _endpoint("/api/experiments/skill-trial", "POST")(
        data=SkillTrialCreate(
            skill="review_pull_request",
            model="claude-haiku-4-5",
            case_ids=["pr-review-v1"],
            source="staged",
            base="both",
            reuse_measured=False,
        ),
        session=session,
    )
    ids = [row["experiment_id"] for row in created["experiments"]]
    assert len(ids) == 2
    families = {row["family_id"] for row in created["experiments"]}
    assert len(families) == 1
    bases = {}
    for experiment_id in ids:
        arms = _arms(session, experiment_id)
        # Each A/B varies the skill only: the base is identical in both arms.
        assert (
            arms["control"]["skills_disabled"] == arms["treatment"]["skills_disabled"]
        )
        bases[experiment_id] = bool(arms["control"]["skills_disabled"])
    assert sorted(bases.values()) == [False, True]  # one full, one minimal
    assert session.exec(select(Run)).all() == []


def _finish_runs(session, experiment_id: str, per_arm: int) -> None:
    """Record ``per_arm`` completed runs on each arm of ``experiment_id``."""
    from harness_lab_app.models import RunConfig, RunState

    config = RunConfig(case_id="pr-review-v1", prompt_variant="harness")
    session.add(config)
    session.commit()
    session.refresh(config)
    arms = session.exec(
        select(ExperimentCondition).where(
            ExperimentCondition.experiment_id == experiment_id
        )
    ).all()
    for arm in arms:
        for index in range(per_arm):
            session.add(
                Run(
                    run_id=f"{experiment_id}-{arm.label}-{index}"[:120],
                    run_config_id=config.id,
                    state=RunState.completed,
                    experiment_id=experiment_id,
                    condition_label=arm.label,
                )
            )
    session.commit()


def _trial(session, base: str, reps: int = 3):
    return _endpoint("/api/experiments/skill-trial", "POST")(
        data=SkillTrialCreate(
            skill="review_pull_request",
            model="claude-haiku-4-5",
            case_ids=["pr-review-v1"],
            source="staged",
            base=base,
            repetitions=reps,
        ),
        session=session,
    )


@pytest.fixture
def staged_review(session, staging):
    _seed(session)
    _endpoint("/api/cases/{case_id}/candidate-skills/{name}/stage", "POST")(
        case_id="pr-review-v1",
        name="review_pull_request",
        replace=False,
        session=session,
    )


def test_both_reuses_a_measured_full_trial_and_creates_only_minimal(
    session, staged_review
):
    full_id = _trial(session, "full", reps=3)["experiments"][0]["experiment_id"]
    _finish_runs(session, full_id, per_arm=3)
    second = _trial(session, "both", reps=3)
    assert [row["experiment_id"] for row in second["reused"]] == [full_id]
    assert len(second["experiments"]) == 1
    arms = _arms(session, second["experiments"][0]["experiment_id"])
    assert arms["control"]["skills_disabled"] == profile_disabled("minimal")


def test_a_draft_that_never_ran_is_not_reused(session, staged_review):
    """Reused means measured: an experiment with no finished run is not data."""
    _trial(session, "full", reps=3)
    second = _trial(session, "both", reps=3)
    assert second["reused"] == []
    assert len(second["experiments"]) == 2


def test_fewer_measured_repetitions_than_asked_are_not_reused(session, staged_review):
    full_id = _trial(session, "full", reps=1)["experiments"][0]["experiment_id"]
    _finish_runs(session, full_id, per_arm=1)
    second = _trial(session, "both", reps=5)
    assert second["reused"] == []


def test_an_edited_staged_skill_is_not_reused(session, staged_review):
    """Same name, new content: the old measurement is of the old skill."""
    from harness_lab_app.staged_skill_service import StagedSkillService

    full_id = _trial(session, "full", reps=3)["experiments"][0]["experiment_id"]
    _finish_runs(session, full_id, per_arm=3)
    service = StagedSkillService(session)
    skill = service.get_skill("review_pull_request")
    service.update_skill(
        "review_pull_request",
        description=skill.description,
        content=skill.content + "\nDemand a trace per finding.\n",
    )
    second = _trial(session, "both", reps=3)
    assert second["reused"] == []


def test_a_single_base_never_reuses_silently(session, staged_review):
    """The UI offers reuse only for 'both'; a single base always creates."""
    full_id = _trial(session, "full", reps=3)["experiments"][0]["experiment_id"]
    _finish_runs(session, full_id, per_arm=3)
    again = _trial(session, "full", reps=3)
    assert again["reused"] == []
    assert len(again["experiments"]) == 1


def test_the_catalog_default_still_builds_the_add_skill_trial(session):
    _seed(session, "a-case")
    outside_minimal = sorted(profile_disabled("minimal"))[0]
    created = _endpoint("/api/experiments/skill-trial", "POST")(
        data=SkillTrialCreate(skill=outside_minimal, model="m", case_ids=["a-case"]),
        session=session,
    )
    arms = _arms(session, created["experiment_id"])
    assert outside_minimal in arms["control"]["skills_disabled"]
    assert outside_minimal not in arms["treatment"]["skills_disabled"]


def test_an_unknown_base_is_refused():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SkillTrialCreate(skill="x", model="m", case_ids=["a"], base="tiny")


def test_both_with_a_catalog_skill_is_refused_with_a_reason(session):
    _seed(session, "a-case")
    outside_minimal = sorted(profile_disabled("minimal"))[0]
    with pytest.raises(HTTPException) as exc:
        _endpoint("/api/experiments/skill-trial", "POST")(
            data=SkillTrialCreate(
                skill=outside_minimal, model="m", case_ids=["a-case"], base="both"
            ),
            session=session,
        )
    assert exc.value.status_code == 422
    assert "full" in exc.value.detail


def test_a_dry_run_reports_what_would_run_and_writes_nothing(session, staged_review):
    from harness_lab_app.models import Experiment

    full_id = _trial(session, "full", reps=3)["experiments"][0]["experiment_id"]
    _finish_runs(session, full_id, per_arm=3)
    before = {e.experiment_id for e in session.exec(select(Experiment)).all()}
    preview = _endpoint("/api/experiments/skill-trial", "POST")(
        data=SkillTrialCreate(
            skill="review_pull_request",
            model="claude-haiku-4-5",
            case_ids=["pr-review-v1"],
            source="staged",
            base="both",
            repetitions=3,
            dry_run=True,
        ),
        session=session,
    )
    assert [row["base"] for row in preview["reused"]] == ["full"]
    assert [row["base"] for row in preview["experiments"]] == ["minimal"]
    assert {e.experiment_id for e in session.exec(select(Experiment)).all()} == before
