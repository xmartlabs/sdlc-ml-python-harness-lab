"""Comparing models on one case: the same case, the same settings, one arm per model.

A frontier game case is rarely passed outright — chess3d-v1 has 40 checks and a
good run lands at 31 — so a pass/fail rate would read 0% for every model. The
comparison leads with how many checks each model's runs passed, phase by phase,
and what that cost, read from what the experiment already records.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from harness_lab_app.case_service import CaseService
from harness_lab_app.experiment_service import ExperimentService
from harness_lab_app.model_comparison import (
    MODEL_FAMILY_PREFIX,
    model_comparison,
    model_family_id,
)
from harness_lab_app.models import (
    CaseType,
    ConditionRole,
    ExperimentMode,
    RunState,
    Score,
    Validation,
)
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import (
    CaseCreate,
    ExperimentConditionCreate,
    ExperimentCreate,
    RunConfigCreate,
    RunCreate,
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _arm(label: str, harness: str, model: str) -> ExperimentConditionCreate:
    return ExperimentConditionCreate(
        label=label,
        role=ConditionRole.treatment,
        overrides={"harness": harness, "executor_model": model},
    )


def _experiment(session: Session) -> ExperimentService:
    CaseService(session).create_case(
        CaseCreate(
            case_id="chess3d-v1",
            name="3D chess",
            type=CaseType.build_from_scratch,
            prompt_variants={"default": "Build it"},
            validation_commands=["test -f {workspace}/project.godot"],
            scoring_rubric="10 when every check passes.",
        )
    )
    service = ExperimentService(session)
    service.create_experiment(
        ExperimentCreate(
            experiment_id="chess-models",
            question="Which model builds chess3d-v1 best?",
            mode=ExperimentMode.subject_validation,
            subject="model comparison on chess3d-v1",
            case_ids=["chess3d-v1"],
            prompt_variants=["default"],
            family_id=model_family_id("chess3d-v1"),
            conditions=[
                _arm("opus", "claude", "claude-opus-5-5"),
                _arm("bonsai", "opencode", "lmstudio/prism-ml/bonsai-27b"),
            ],
        )
    )
    return service


def _run(
    session: Session,
    run_id: str,
    label: str,
    model: str,
    *,
    state: RunState,
    scores: dict[str, float],
    per_step: list[dict] | None = None,
    checks: tuple[int, int] | None = None,
) -> None:
    run = RunService(session).create_run(
        RunCreate(
            run_id=run_id,
            config=RunConfigCreate(
                case_id="chess3d-v1",
                prompt_variant="default",
                executor_model=model,
                harness="claude" if model.startswith("claude") else "opencode",
                max_steps=5,
            ),
        )
    )
    run.experiment_id = "chess-models"
    run.condition_label = label
    run.state = state
    session.add(run)
    for name, value in scores.items():
        session.add(Score(run_id=run_id, name=name, value_num=value))
    objective: dict = {}
    if checks is not None:
        passed, total = checks
        objective["checks"] = [{"passed": i < passed} for i in range(total)]
    if per_step is not None:
        objective["per_step"] = {"steps": per_step}
    if objective:
        session.add(Validation(run_id=run_id, objective=objective))
    session.commit()


def test_a_model_family_groups_every_comparison_on_one_case() -> None:
    assert model_family_id("chess3d-v1") == f"{MODEL_FAMILY_PREFIX}chess3d-v1"


def test_the_comparison_reads_each_models_checks_phases_and_cost(
    session: Session,
) -> None:
    service = _experiment(session)
    _run(
        session,
        "chess3d-v1-K-010",
        "opus",
        "claude-opus-5-5",
        state=RunState.completed,
        scores={"test_pass_rate": 0.775, "cost_usd": 4.2, "wall_time_minutes": 61.0},
        checks=(31, 40),
        per_step=[
            {"step": 1, "passed": 17, "total": 40},
            {"step": 2, "passed": 24, "total": 40},
            {"step": 3, "passed": 31, "total": 40},
            {"step": 4, "progress": None, "source": "unmeasured"},
        ],
    )
    _run(
        session,
        "chess3d-v1-K-011",
        "bonsai",
        "lmstudio/prism-ml/bonsai-27b",
        state=RunState.failed,
        scores={"test_pass_rate": 0.075, "wall_time_minutes": 160.0},
        checks=(3, 40),
        per_step=[
            {"step": 1, "passed": 3, "total": 40},
            {"step": 2, "passed": 3, "total": 40},
        ],
    )

    result = model_comparison(service, "chess-models")

    assert result["case_id"] == "chess3d-v1"
    assert result["family_id"] == "models-chess3d-v1"
    arms = {arm["label"]: arm for arm in result["arms"]}
    # Declared order, each arm named by what it ran.
    assert [arm["label"] for arm in result["arms"]] == ["opus", "bonsai"]
    assert arms["opus"]["harness"] == "claude"
    assert arms["opus"]["executor_model"] == "claude-opus-5-5"
    opus = arms["opus"]["runs"][0]
    assert opus["run_id"] == "chess3d-v1-K-010"
    assert opus["state"] == "completed"
    assert (opus["passed"], opus["total"]) == (31, 40)
    assert opus["pass_rate"] == 0.775
    assert opus["cost_usd"] == 4.2 and opus["minutes"] == 61.0
    # Phase by phase; an unmeasured phase stays visible as None.
    assert opus["phases"] == [
        {"step": 1, "passed": 17, "total": 40},
        {"step": 2, "passed": 24, "total": 40},
        {"step": 3, "passed": 31, "total": 40},
        {"step": 4, "passed": None, "total": None},
    ]
    assert arms["opus"]["best_pass_rate"] == 0.775
    assert arms["opus"]["mean_pass_rate"] == 0.775
    bonsai = arms["bonsai"]["runs"][0]
    # A local model reports no cost: unknown, never zero.
    assert bonsai["cost_usd"] is None
    assert (bonsai["passed"], bonsai["total"]) == (3, 40)


def test_an_arm_that_has_not_run_yet_is_listed_empty(session: Session) -> None:
    service = _experiment(session)
    result = model_comparison(service, "chess-models")
    for arm in result["arms"]:
        assert arm["runs"] == []
        assert arm["mean_pass_rate"] is None and arm["best_pass_rate"] is None
