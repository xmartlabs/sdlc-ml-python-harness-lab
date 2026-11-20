"""A case that ships a skill offers three paths from the UI (ADR-0091).

Try the case's candidate, improve it, or bring your own — each one stages a
skill and creates the same two-arm trial **New evaluation → Add skills** builds
with Enforce on. Nothing is launched here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select

from harness_lab_app import staged_skill_service as service_module
from harness_lab_app.models import (
    Case,
    CaseType,
    ExperimentCondition,
    Run,
)
from harness_lab_app.schemas import SkillTrialCreate
from harness_lab_app.skill_trial import CANDIDATE_FAMILY


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
    """Staged files go to a temp dir, never to the developer's data/."""
    monkeypatch.setattr(
        service_module, "get_settings", lambda: SimpleNamespace(repo_root=tmp_path)
    )
    monkeypatch.setattr(service_module, "known_skill_names", lambda: set())
    return tmp_path


def _seed_case(session, case_id: str = "pr-review-v1") -> None:
    session.add(
        Case(
            case_id=case_id,
            name=case_id,
            type=CaseType.bugfix,
            prompt_variants={"default": "review it", "harness": "review it, governed"},
        )
    )
    session.commit()


def test_a_case_lists_the_skill_it_ships(session, staging):
    listed = _endpoint("/api/cases/{case_id}/candidate-skills", "GET")(
        case_id="pr-review-v1", session=session
    )
    assert [row["name"] for row in listed] == ["review_pull_request"]
    assert listed[0]["source"] == (
        "benchmarks/pr-review-v1/candidate-skill/review_pull_request"
    )
    assert listed[0]["staged"] is False


def test_a_case_with_no_candidate_lists_nothing(session, staging):
    assert (
        _endpoint("/api/cases/{case_id}/candidate-skills", "GET")(
            case_id="clinic-slots-t1", session=session
        )
        == []
    )


def test_staging_from_the_ui_marks_the_candidate_staged(session, staging):
    stage = _endpoint("/api/cases/{case_id}/candidate-skills/{name}/stage", "POST")
    result = stage(
        case_id="pr-review-v1",
        name="review_pull_request",
        replace=False,
        session=session,
    )
    assert result["name"] == "review_pull_request"
    assert result["files"] >= 1
    listed = _endpoint("/api/cases/{case_id}/candidate-skills", "GET")(
        case_id="pr-review-v1", session=session
    )
    assert listed[0]["staged"] is True
    # Staging twice without replace keeps local edits: it is refused, not overwritten.
    with pytest.raises(HTTPException) as exc:
        stage(
            case_id="pr-review-v1",
            name="review_pull_request",
            replace=False,
            session=session,
        )
    assert exc.value.status_code == 422


def test_staging_a_skill_the_case_does_not_ship_is_a_404(session, staging):
    stage = _endpoint("/api/cases/{case_id}/candidate-skills/{name}/stage", "POST")
    with pytest.raises(HTTPException) as exc:
        stage(case_id="pr-review-v1", name="nope", replace=False, session=session)
    assert exc.value.status_code == 404


def test_a_staged_trial_has_the_arms_the_first_candidate_trials_used(session, staging):
    _seed_case(session)
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
        ),
        session=session,
    )
    assert created["family_id"] == CANDIDATE_FAMILY
    arms = {
        c.label: c.overrides
        for c in session.exec(
            select(ExperimentCondition).where(
                ExperimentCondition.experiment_id == created["experiment_id"]
            )
        ).all()
    }
    pinned = {
        "executor_model": "claude-haiku-4-5",
        "harness": "claude",
        "prompt_variant": "harness",
    }
    assert arms == {
        "control": {**pinned, "skills_extra": [], "skills_invoke": []},
        "review-pull-request-enforced": {
            **pinned,
            "skills_extra": ["review_pull_request"],
            "skills_invoke": ["review_pull_request"],
        },
    }
    assert session.exec(select(Run)).all() == []


def test_a_staged_trial_of_a_skill_not_staged_is_refused(session, staging):
    _seed_case(session)
    with pytest.raises(HTTPException) as exc:
        _endpoint("/api/experiments/skill-trial", "POST")(
            data=SkillTrialCreate(
                skill="review_pull_request",
                model="m",
                case_ids=["pr-review-v1"],
                source="staged",
            ),
            session=session,
        )
    assert exc.value.status_code == 422
    assert "not staged" in exc.value.detail
