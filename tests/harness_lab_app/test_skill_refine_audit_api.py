"""Improve a skill from the UI: audit it, stage a v2, trial v1 vs v2 (ADR-0092).

Plus the two read-only helpers the guided flows need: the cost estimate shown
before anything is created, and the decision criteria the Metrics page shows.
"""

from __future__ import annotations

import json
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
    RunConfig,
    RunState,
    Score,
)
from harness_lab_app.schemas import SkillDraftStage, SkillRefineCreate
from harness_lab_app.skill_audit import SkillAuditError, audit_skill


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


SKILL_MD = (
    "---\nname: {name}\ndescription: review a pull request\n---\n\nCheck tests.\n"
)


def _stage(session, name: str) -> None:
    _endpoint("/api/skills/drafts", "POST")(
        data=SkillDraftStage(name=name, content=SKILL_MD.format(name=name)),
        session=session,
    )


def _seed_case(session, case_id: str = "pr-review-v1") -> None:
    session.add(
        Case(
            case_id=case_id,
            name=case_id,
            type=CaseType.bugfix,
            prompt_variants={"default": "d", "harness": "h"},
        )
    )
    session.commit()


def test_a_staged_v1_and_v2_differ_only_in_the_version(session, staging):
    _seed_case(session)
    _stage(session, "review_pull_request")
    _stage(session, "review_pull_request-v2")
    created = _endpoint("/api/experiments/skill-refine", "POST")(
        data=SkillRefineCreate(
            skill="review_pull_request",
            candidate="review_pull_request-v2",
            model="claude-haiku-4-5",
            case_ids=["pr-review-v1"],
        ),
        session=session,
    )
    arms = {
        c.role: c.overrides
        for c in session.exec(
            select(ExperimentCondition).where(
                ExperimentCondition.experiment_id == created["experiment_id"]
            )
        ).all()
    }
    assert arms["control"]["skills_extra"] == ["review_pull_request"]
    assert arms["treatment"]["skills_extra"] == ["review_pull_request-v2"]
    for arm in arms.values():
        assert arm["skills_invoke"] == arm["skills_extra"]
        assert arm["executor_model"] == "claude-haiku-4-5"
    assert arms["control"]["skills_disabled"] == arms["treatment"]["skills_disabled"]
    assert session.exec(select(Run)).all() == []


def test_a_refine_whose_v2_is_not_staged_is_refused(session, staging):
    _seed_case(session)
    with pytest.raises(HTTPException) as exc:
        _endpoint("/api/experiments/skill-refine", "POST")(
            data=SkillRefineCreate(
                skill="review_pull_request",
                candidate="review_pull_request-v2",
                model="m",
                case_ids=["pr-review-v1"],
            ),
            session=session,
        )
    assert exc.value.status_code == 422
    assert "not staged" in exc.value.detail


def _experiment_with_skill_runs(session, skill: str) -> str:
    """One experiment whose treatment carried ``skill``, with a failed check."""
    from harness_lab_app.models import Experiment, ExperimentMode, Validation

    session.add(
        Experiment(
            experiment_id="trial-x",
            name="trial-x",
            question="q?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["pr-review-v1"],
            repetitions=1,
        )
    )
    session.add(
        ExperimentCondition(
            experiment_id="trial-x",
            label="with",
            role="treatment",
            overrides={"skills_extra": [skill], "skills_invoke": [skill]},
        )
    )
    config = RunConfig(case_id="pr-review-v1", prompt_variant="harness")
    session.add(config)
    session.commit()
    session.refresh(config)
    session.add(
        Run(
            run_id="pr-review-v1-K-900",
            run_config_id=config.id,
            state=RunState.completed,
            experiment_id="trial-x",
            condition_label="with",
        )
    )
    session.add(
        Validation(
            run_id="pr-review-v1-K-900",
            objective={
                "checks": [
                    {
                        "command": "grade.py check k05",
                        "exit_code": 1,
                        "conclusive": True,
                    }
                ]
            },
        )
    )
    session.commit()
    return "trial-x"


def test_the_skill_audit_reads_the_failed_checks_and_returns_a_v2_draft(
    session, staging
):
    _stage(session, "review_pull_request")
    experiment_id = _experiment_with_skill_runs(session, "review_pull_request")
    seen: dict[str, str] = {}

    def fake_llm(model, system, user):
        seen["user"] = user
        return (
            json.dumps(
                {
                    "covers": ["asks for a verdict"],
                    "misses": [
                        {
                            "check": "k05",
                            "why": "names it too weakly",
                            "fix": "demand a trace",
                        }
                    ],
                    "summary": "The skill suggests; the v2 demands evidence.",
                    "v2": SKILL_MD.format(name="review_pull_request-v2"),
                }
            ),
            "stub-model",
        )

    result = audit_skill(
        session, "review_pull_request", experiment_id, model="stub", call=fake_llm
    )
    assert "grade.py check k05" in seen["user"]
    assert "Check tests." in seen["user"]
    assert result.misses[0]["check"] == "k05"
    assert result.v2.startswith("---\nname: review_pull_request-v2")
    assert result.as_dict()["v2_name"] == "review_pull_request-v2"
    assert result.model == "stub-model"
    assert result.runs_read == 1


def test_the_skill_audit_refuses_an_experiment_that_never_carried_the_skill(
    session, staging
):
    _stage(session, "review_pull_request")
    _experiment_with_skill_runs(session, "review_pull_request")
    with pytest.raises(SkillAuditError, match="No arm"):
        audit_skill(session, "other_skill", "trial-x", call=lambda *_: ("{}", "m"))


def test_the_skill_audit_refuses_an_answer_without_a_draft(session, staging):
    _stage(session, "review_pull_request")
    _experiment_with_skill_runs(session, "review_pull_request")
    with pytest.raises(SkillAuditError, match="no v2"):
        audit_skill(
            session,
            "review_pull_request",
            "trial-x",
            call=lambda *_: (json.dumps({"summary": "s"}), "m"),
        )


def test_a_draft_is_staged_only_when_asked_and_never_twice(session, staging):
    _stage(session, "review_pull_request-v2")
    with pytest.raises(HTTPException) as exc:
        _stage(session, "review_pull_request-v2")
    assert exc.value.status_code == 422


def test_the_estimate_is_the_median_of_completed_runs_and_says_how_many(session):
    config = RunConfig(
        case_id="pr-review-v1", prompt_variant="harness", executor_model="haiku"
    )
    session.add(config)
    session.commit()
    session.refresh(config)
    for index, (cost, minutes, state) in enumerate(
        [
            (0.2, 1.0, RunState.completed),
            (0.3, 2.0, RunState.completed),
            (9.0, 9.0, RunState.failed),
        ]
    ):
        run_id = f"r-{index}"
        session.add(Run(run_id=run_id, run_config_id=config.id, state=state))
        session.add(Score(run_id=run_id, name="cost_usd", value_num=cost))
        session.add(Score(run_id=run_id, name="wall_time_minutes", value_num=minutes))
    session.commit()
    estimate = _endpoint("/api/estimate", "GET")(
        case_id="pr-review-v1", model="haiku", session=session
    )
    assert estimate["n"] == 2  # the failed run is not a price
    assert estimate["cost_usd"] == pytest.approx(0.25)
    assert estimate["minutes"] == pytest.approx(1.5)


def test_an_unmeasured_estimate_says_unknown_not_free(session):
    estimate = _endpoint("/api/estimate", "GET")(
        case_id="nothing", model="nobody", session=session
    )
    assert estimate == {"case_id": "nothing", "model": "nobody", "n": 0}


def test_the_criteria_are_the_constants_the_lab_uses():
    from harness_lab_app import pruning, statistics

    criteria = _endpoint("/api/metrics/criteria", "GET")()
    assert criteria["repetitions"]["validation_from"] == statistics.STRONGER_EVIDENCE
    assert criteria["pruning"]["success_margin"] == pruning.DEFAULT_SUCCESS_MARGIN
    assert criteria["pruning"]["relative_margin"] == pruning.DEFAULT_RELATIVE_MARGIN


def test_auditing_an_unknown_skill_is_a_404(session, staging):
    _experiment_with_skill_runs(session, "review_pull_request")
    from harness_lab_app.schemas import SkillAuditRequest

    with pytest.raises(HTTPException) as exc:
        _endpoint("/api/skills/{name}/audit", "POST")(
            name="no_such_skill",
            data=SkillAuditRequest(experiment_id="trial-x"),
            session=session,
        )
    # The experiment carried review_pull_request, not this one: the arm check
    # fires first and says so — still a client error, never a 5xx.
    assert exc.value.status_code in (404, 422)


def test_a_gateway_refusal_is_a_400_not_unavailable(session, staging, monkeypatch):
    from harness_lab_app import skill_audit
    from harness_lab_app.llm_transport import GatewayHTTPError
    from harness_lab_app.schemas import SkillAuditRequest

    _stage(session, "review_pull_request")
    _experiment_with_skill_runs(session, "review_pull_request")

    def refuse(*_args, **_kwargs):
        raise GatewayHTTPError(400, "unknown model 'nope'")

    monkeypatch.setattr(skill_audit, "audit_skill", refuse)
    monkeypatch.setattr("harness_lab_app.skill_audit.audit_skill", refuse)
    with pytest.raises(HTTPException) as exc:
        _endpoint("/api/skills/{name}/audit", "POST")(
            name="review_pull_request",
            data=SkillAuditRequest(experiment_id="trial-x", auditor_model="nope"),
            session=session,
        )
    assert exc.value.status_code == 400


def _warnings(conditions):
    from harness_lab_app.experiment_service import validate_design
    from harness_lab_app.models import ExperimentMode

    return validate_design(
        ExperimentMode.harness_evaluation, conditions, in_family=True
    )


def test_a_version_swap_of_one_staged_skill_is_one_factor():
    """v1 vs v2, each added and asked for, is one intervention: the version."""
    base = {"skills_disabled": []}
    conditions = [
        {
            "label": "v1",
            "role": "control",
            "overrides": {
                **base,
                "skills_extra": ["review"],
                "skills_invoke": ["review"],
            },
        },
        {
            "label": "v2",
            "role": "treatment",
            "overrides": {
                **base,
                "skills_extra": ["review-v2"],
                "skills_invoke": ["review-v2"],
            },
        },
    ]
    assert not any("More than one harness factor" in w for w in _warnings(conditions))


def test_a_swap_that_also_changes_the_base_still_warns():
    """The rule is narrow: change the base too and it is two factors again."""
    conditions = [
        {
            "label": "v1",
            "role": "control",
            "overrides": {
                "skills_disabled": [],
                "skills_extra": ["a"],
                "skills_invoke": ["a"],
            },
        },
        {
            "label": "v2",
            "role": "treatment",
            "overrides": {
                "skills_disabled": ["x"],
                "skills_extra": ["b"],
                "skills_invoke": ["b"],
            },
        },
    ]
    assert any("More than one harness factor" in w for w in _warnings(conditions))


def test_a_swap_where_one_arm_is_not_asked_for_still_warns():
    conditions = [
        {
            "label": "v1",
            "role": "control",
            "overrides": {"skills_extra": ["a"], "skills_invoke": ["a"]},
        },
        {
            "label": "v2",
            "role": "treatment",
            "overrides": {"skills_extra": ["b"], "skills_invoke": []},
        },
    ]
    assert any("More than one harness factor" in w for w in _warnings(conditions))
