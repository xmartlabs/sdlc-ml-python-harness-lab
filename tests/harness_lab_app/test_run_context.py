"""The condition a run *was* — the questions a log is only readable against (§16).

A run page that opens with stdout answers "what happened inside the container?"
before anyone has established which arm of which design produced it. These
tests pin the fields that make a log interpretable, and one refusal: the commit
is the reproducible fact, so it is reported as resolved rather than as the ref
that was asked for.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.api import create_app
from harness_lab_app.models import (
    Case,
    Experiment,
    ExperimentMode,
    HarnessSource,
    Run,
    RunConfig,
    TargetRepository,
)


def _endpoint(app, path: str):
    for route in app.router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"Route not found: {path}")


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _add_run(session: Session, run_id: str, *, config: dict, **run_over) -> None:
    """Insert the rows directly.

    Going through RunService would resolve the target ref over the network, and
    what is under test here is how a recorded run reads back — not how one is
    created.
    """
    row = RunConfig(**config)
    session.add(row)
    session.commit()
    session.refresh(row)
    session.add(Run(run_id=run_id, run_config_id=row.id or 0, **run_over))
    session.commit()


def _seed(session: Session, **config_over) -> None:
    session.add(
        Case(
            case_id="tetris-v1",
            name="Tetris in one file",
            type="single_prompt",
            validation_commands=["pytest -q"],
        )
    )
    session.add(
        TargetRepository(
            target_id="acme-api",
            name="Acme API",
            repository_url="https://example.invalid/acme/api.git",
            default_ref="main",
            validation_commands=["make test"],
        )
    )
    session.add(
        Experiment(
            experiment_id="skill-x",
            name="Skill X",
            question="Does skill X reduce steps?",
            mode=ExperimentMode.harness_evaluation,
        )
    )
    session.commit()

    base = {
        "case_id": "tetris-v1",
        "prompt_variant": "sdlc",
        "executor_model": "stub-executor",
        "target_repo_id": "acme-api",
        "target_ref": "main",
        "target_commit": "abc123def456789",
        "harness_source": HarnessSource.native,
    }
    base.update(config_over)
    _add_run(
        session,
        "acme-api-J-001",
        config=base,
        experiment_id="skill-x",
        condition_label="baseline",
    )


def test_context_names_the_experiment_arm_the_run_belongs_to():
    app = create_app()
    with _session() as session:
        _seed(session)

        context = _endpoint(app, "/api/runs/{run_id}/context")(
            "acme-api-J-001", session
        )

    assert context["experiment_id"] == "skill-x"
    assert context["condition_label"] == "baseline"
    assert context["experiment_question"] == "Does skill X reduce steps?"
    assert context["experiment_mode"] == "harness_evaluation"


def test_context_reports_the_resolved_commit_not_just_the_ref():
    """The ref moves; the commit is what makes the run reproducible (ADR-0029)."""
    app = create_app()
    with _session() as session:
        _seed(session)

        context = _endpoint(app, "/api/runs/{run_id}/context")(
            "acme-api-J-001", session
        )

    assert context["target_ref"] == "main"
    assert context["target_commit"] == "abc123def456789"


def test_context_carries_the_governance_condition():
    """Which governance the agent could see is a condition, not a detail (ADR-0031)."""
    app = create_app()
    with _session() as session:
        _seed(session)

        context = _endpoint(app, "/api/runs/{run_id}/context")(
            "acme-api-J-001", session
        )

    assert context["harness_source"] == "native"


def test_context_prefers_the_targets_own_validation_gate():
    """A target repository's tests outrank a rubric the lab invented for it."""
    app = create_app()
    with _session() as session:
        _seed(session)

        context = _endpoint(app, "/api/runs/{run_id}/context")(
            "acme-api-J-001", session
        )

    assert context["validation_commands"] == ["make test"]


def test_context_of_an_ad_hoc_run_says_so_rather_than_inventing_a_design():
    app = create_app()
    with _session() as session:
        session.add(Case(case_id="tetris-v1", name="Tetris", type="single_prompt"))
        session.commit()
        _add_run(
            session,
            "tetris-v1-J-002",
            config={
                "case_id": "tetris-v1",
                "prompt_variant": "sdlc",
                "executor_model": "stub-executor",
            },
        )

        context = _endpoint(app, "/api/runs/{run_id}/context")(
            "tetris-v1-J-002", session
        )

    assert context["experiment_id"] == ""
    assert context["condition_label"] == ""
    assert context["target_repo_id"] == ""
    # Never inside an external workspace, so never a governance comparison.
    assert context["harness_source"] == "lab_root"


def test_context_of_an_unknown_run_is_a_404():
    app = create_app()
    with _session() as session, pytest.raises(HTTPException) as exc:
        _endpoint(app, "/api/runs/{run_id}/context")("nope", session)

    assert exc.value.status_code == 404
