"""The LLM reading of a finished experiment (ADR-0053).

The reading is prose over evidence the lab already computed. These tests pin
the evidence document (what the model is allowed to see), the fixed sections
the prompt demands, the "every run has stopped" gate, and the failure path —
never the model's words.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from harness_lab_app.experiment_audit_service import (
    EXPERIMENT_AUDIT_SYSTEM_PROMPT,
    ExperimentAuditService,
    build_evidence,
    render_audit_prompt,
    runs_finished,
)
from harness_lab_app.experiment_service import ExperimentService
from harness_lab_app.models import (
    Attribution,
    Case,
    CaseType,
    ConditionRole,
    ExperimentMode,
    RunState,
    Score,
    Validation,
)
from harness_lab_app.schemas import (
    ExperimentConditionCreate,
    ExperimentCreate,
    RunConfigCreate,
)
from harness_lab_app.skills_catalog import known_skill_names
from harness_lab_app.tasks import _audit_experiment_body

# A governed skill the treatment can be asked to invoke; preflight refuses a
# name the run could not see.
SKILL = sorted(known_skill_names())[0]


def _launch(session, experiment_id: str = "read-me"):
    session.add(
        Case(
            case_id="tetris-v2",
            name="Tetris",
            type=CaseType.build_from_scratch,
            prompt_variants={"default": "Build it.", "harness": "Governed build."},
        )
    )
    session.commit()
    service = ExperimentService(session)
    service.create_experiment(
        ExperimentCreate(
            experiment_id=experiment_id,
            name="Read me",
            question="Does the full harness help?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["tetris-v2"],
            repetitions=1,
            conditions=[
                ExperimentConditionCreate(
                    label="bare",
                    role=ConditionRole.control,
                    overrides={"harness_source": "bare", "prompt_variant": "default"},
                ),
                ExperimentConditionCreate(
                    label="full-harness",
                    role=ConditionRole.treatment,
                    overrides={"prompt_variant": "harness", "skills_invoke": [SKILL]},
                ),
            ],
        )
    )
    runs = service.launch(
        experiment_id,
        RunConfigCreate(
            case_id="tetris-v2",
            prompt_variant="default",
            executor_model="stub",
            score_mode="manual",
            max_steps=3,
        ),
    )
    return service, runs


def _finish(session, run, *, passed: bool, cost: float, docs_read=(), skills=()):
    run.state = RunState.completed
    session.add(run)
    session.add(
        Score(
            run_id=run.run_id, name="task_validation_pass", version=1, value_bool=passed
        )
    )
    session.add(Score(run_id=run.run_id, name="cost_usd", version=1, value_num=cost))
    session.add(
        Validation(
            run_id=run.run_id,
            objective={
                "passed": passed,
                "summary": "12/12 checks passed" if passed else "9/12 checks passed",
                "checks": [
                    {
                        "name": "pytest ::x",
                        "passed": passed,
                        "exit_code": 0 if passed else 1,
                        "detail": "boom",
                    }
                ],
            },
            subjective={
                "functional_score": 10 if passed else 6,
                "score_source": "llm_judge",
                "notes": "level",
            },
            effort={"stop_reason": "completion_signal"},
        )
    )
    session.add(
        Attribution(
            run_id=run.run_id,
            harness_usage={
                "governance_docs_available": ["CLAUDE.md", ".github/standards.md"],
                "governance_docs_read": list(docs_read),
                "skills_available": [SKILL, "other-skill"],
                "skills_consulted": list(skills),
                "governance_source": "lab_root",
            },
        )
    )
    session.commit()


def test_the_gate_waits_for_the_last_run(session):
    _, runs = _launch(session)

    assert runs_finished(session, "read-me") == (False, 0, 2)
    _finish(session, runs[0], passed=True, cost=0.4)
    assert runs_finished(session, "read-me") == (False, 1, 2)
    _finish(session, runs[1], passed=True, cost=1.2)
    assert runs_finished(session, "read-me") == (True, 2, 2)


def test_the_endpoint_refuses_a_half_finished_experiment(session):
    from harness_lab_app.api import create_app

    _, runs = _launch(session)
    _finish(session, runs[0], passed=True, cost=0.4)
    app = create_app()
    endpoint = next(
        route.endpoint
        for route in app.router.routes
        if getattr(route, "path", None) == "/api/experiments/{experiment_id}/audit"
        and "POST" in getattr(route, "methods", set())
    )

    with pytest.raises(HTTPException) as exc:
        endpoint("read-me", None, session)

    assert exc.value.status_code == 409
    assert "1 of 2" in str(exc.value.detail)
    with pytest.raises(HTTPException) as missing:
        endpoint("ghost", None, session)
    assert missing.value.status_code == 404


def test_the_evidence_says_what_each_arm_used_and_did_not(session):
    _, runs = _launch(session)
    bare = next(r for r in runs if r.condition_label == "bare")
    full = next(r for r in runs if r.condition_label == "full-harness")
    _finish(session, bare, passed=True, cost=0.4)
    _finish(session, full, passed=True, cost=1.2, docs_read=["CLAUDE.md"], skills=[])

    evidence = build_evidence(session, "read-me")

    assert evidence["question"] == "Does the full harness help?"
    assert [arm["label"] for arm in evidence["arms"]] == ["bare", "full-harness"]
    by_arm = {row["arm"]: row for row in evidence["runs"]}
    usage = by_arm["full-harness"]["usage"]
    assert usage["governance_docs_read"] == ["CLAUDE.md"]
    assert usage["governance_docs_not_read"] == [".github/standards.md"]
    # Asked for `a`, opened nothing: the fact the reading exists to state.
    assert usage["skills_asked_for_but_not_invoked"] == [SKILL]
    assert usage["skills_available_never_opened"] == [SKILL, "other-skill"]
    assert by_arm["bare"]["scores"]["cost_usd"] == 0.4
    assert by_arm["full-harness"]["judge"]["source"] == "llm_judge"
    # The lab's own validity verdict travels with the evidence, verbatim.
    assert evidence["validity"]["status"] in ("valid", "degraded", "invalid")
    assert evidence["verdict"]["headline"]


def test_the_prompt_demands_the_fixed_sections():
    for section in (
        "## Veredicto",
        "## Qué se comparó",
        "## Resultado",
        "## Qué se usó y qué no",
        "## Por qué (no) se puede confiar",
        "## Qué hacer ahora",
    ):
        assert section in EXPERIMENT_AUDIT_SYSTEM_PROMPT
    assert "significativo" in EXPERIMENT_AUDIT_SYSTEM_PROMPT  # named so it is banned
    user = render_audit_prompt({"experiment_id": "x", "runs": []})
    assert '"experiment_id": "x"' in user


def test_the_reading_is_stored_with_its_evidence(session):
    _, runs = _launch(session)
    for run in runs:
        _finish(session, run, passed=True, cost=0.5)
    seen: dict = {}

    def gateway(model: str, system: str, user: str) -> str:
        seen.update(model=model, system=system, user=user)
        return "## Veredicto\nEmpate.\n"

    _audit_experiment_body(session, "read-me", "judge-x", gateway=gateway)

    audit = ExperimentAuditService(session).get("read-me")
    assert audit is not None
    assert audit.status == "done"
    assert audit.auditor_model == "judge-x"
    assert audit.markdown.startswith("## Veredicto")
    assert audit.evidence_json["experiment_id"] == "read-me"
    assert seen["model"] == "judge-x"
    assert seen["system"] == EXPERIMENT_AUDIT_SYSTEM_PROMPT


def test_a_failed_reading_is_recorded_not_raised(session):
    _, runs = _launch(session)
    for run in runs:
        _finish(session, run, passed=True, cost=0.5)

    def gateway(*_args) -> str:
        raise RuntimeError("gateway down")

    _audit_experiment_body(session, "read-me", "judge-x", gateway=gateway)

    audit = ExperimentAuditService(session).get("read-me")
    assert audit is not None
    assert audit.status == "failed"
    assert "gateway down" in (audit.error or "")
