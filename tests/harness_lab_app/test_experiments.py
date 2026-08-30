"""Experiments: a declared question and a design the mode can actually answer.

The tests that matter here are the *rejections*. An experiment that varies two
factors at once is a valid request and an invalid measurement, and the whole
point of the abstraction is that the lab says so at creation instead of
rendering the average later (ADR-0028, ADR-0033).
"""

from __future__ import annotations

import pytest

from harness_lab_app.case_service import CaseService
from harness_lab_app.experiment_service import (
    ExperimentDesignError,
    ExperimentError,
    ExperimentService,
    repetition_warning,
    validate_design,
)
from harness_lab_app.models import (
    CaseType,
    ConditionRole,
    ExperimentMode,
    ExperimentStatus,
    HarnessSource,
)
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import (
    CaseCreate,
    ExperimentConditionCreate,
    ExperimentCreate,
    RunConfigCreate,
    TargetRepositoryCreate,
)
from harness_lab_app.target_service import TargetService

_SHA = "d" * 40


def _condition(label, role=ConditionRole.treatment, **overrides):
    return {"label": label, "role": role, "overrides": overrides}


# --------------------------------------------------------------------------
# Design validation — the rejections are the feature
# --------------------------------------------------------------------------


def test_a_harness_experiment_may_not_also_vary_the_repository():
    with pytest.raises(ExperimentDesignError, match="cannot be attributed"):
        validate_design(
            ExperimentMode.harness_evaluation,
            [
                _condition(
                    "control",
                    ConditionRole.control,
                    target_repo_id="a",
                    harness_source=HarnessSource.native,
                ),
                _condition(
                    "treatment",
                    target_repo_id="b",
                    harness_source=HarnessSource.injected,
                ),
            ],
        )


def test_a_harness_experiment_may_not_also_vary_the_model():
    with pytest.raises(ExperimentDesignError, match="confounded"):
        validate_design(
            ExperimentMode.harness_evaluation,
            [
                _condition(
                    "control",
                    ConditionRole.control,
                    executor_model="m1",
                    harness_source=HarnessSource.native,
                ),
                _condition(
                    "treatment",
                    executor_model="m2",
                    harness_source=HarnessSource.injected,
                ),
            ],
        )


def test_a_harness_experiment_must_vary_something_about_the_harness():
    with pytest.raises(ExperimentDesignError, match="nothing to attribute"):
        validate_design(
            ExperimentMode.harness_evaluation,
            [
                _condition("control", ConditionRole.control, max_steps=5),
                _condition("treatment", max_steps=10),
            ],
        )


def test_varying_two_harness_factors_warns_without_blocking():
    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            _condition(
                "control",
                ConditionRole.control,
                harness_source=HarnessSource.native,
                skills_disabled=[],
            ),
            _condition(
                "treatment",
                harness_source=HarnessSource.injected,
                skills_disabled=["brainstorming"],
            ),
        ],
    )
    assert any("more than one harness factor" in w.lower() for w in warnings)


def test_a_comparison_needs_exactly_one_control():
    with pytest.raises(ExperimentDesignError, match="exactly one control"):
        validate_design(
            ExperimentMode.harness_evaluation,
            [
                _condition("a", harness_source=HarnessSource.native),
                _condition("b", harness_source=HarnessSource.injected),
            ],
        )


def test_a_cross_repo_experiment_must_vary_the_repository():
    with pytest.raises(ExperimentDesignError, match="all target the same"):
        validate_design(
            ExperimentMode.cross_repo,
            [
                _condition("a", ConditionRole.control, target_repo_id="one"),
                _condition("b", target_repo_id="one"),
            ],
        )


def test_a_cross_repo_experiment_may_not_vary_anything_else():
    with pytest.raises(ExperimentDesignError, match="also vary"):
        validate_design(
            ExperimentMode.cross_repo,
            [
                _condition(
                    "a",
                    ConditionRole.control,
                    target_repo_id="one",
                    executor_model="m1",
                ),
                _condition("b", target_repo_id="two", executor_model="m2"),
            ],
        )


def test_a_valid_cross_repo_design_still_warns_against_pooling():
    warnings = validate_design(
        ExperimentMode.cross_repo,
        [
            _condition("payments", ConditionRole.control, target_repo_id="payments"),
            _condition("inventory", target_repo_id="inventory"),
        ],
    )
    assert any("never pooled" in warning for warning in warnings)


def test_discovery_has_no_design_to_invalidate():
    assert (
        validate_design(
            ExperimentMode.discovery,
            [_condition("look-around", target_repo_id="theirs")],
        )
        == []
    )


@pytest.mark.parametrize(
    ("reps", "expected"),
    [(1, "exploratory only"), (3, "exploratory comparison"), (5, None), (10, None)],
)
def test_low_repetition_counts_say_what_they_do_not_support(reps, expected):
    note = repetition_warning(reps)
    assert (expected in note) if expected else (note is None)


# --------------------------------------------------------------------------
# Creation and launch
# --------------------------------------------------------------------------


@pytest.fixture
def lab(session, monkeypatch):
    CaseService(session).create_case(
        CaseCreate(
            case_id="add-endpoint",
            name="Add endpoint",
            type=CaseType.repo_task,
            prompt_variants={"default": "Add an endpoint"},
        )
    )
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://github.com/acme/payments.git",
        )
    )
    runs = RunService(session)
    monkeypatch.setattr(runs, "_check_runner_image", lambda: None)
    monkeypatch.setattr(runs, "_publish", lambda _run_id: None)
    monkeypatch.setattr(
        "harness_lab_app.run_service.RunService._check_runner_image", lambda _self: None
    )
    monkeypatch.setattr(
        "harness_lab_app.run_service.RunService._publish", lambda _self, _run_id: None
    )
    monkeypatch.setattr(
        "harness_lab_app.run_service.resolve_ref", lambda _url, _ref: _SHA
    )
    return ExperimentService(session)


def _payload(**over) -> ExperimentCreate:
    base = {
        "experiment_id": "migration-safety",
        "question": "Does our harness improve endpoint work on payments?",
        "mode": ExperimentMode.harness_evaluation,
        "case_ids": ["add-endpoint"],
        "prompt_variants": ["default"],
        "repetitions": 5,
        "conditions": [
            ExperimentConditionCreate(
                label="native",
                role=ConditionRole.control,
                overrides={
                    "target_repo_id": "payments",
                    "harness_source": HarnessSource.native,
                },
            ),
            ExperimentConditionCreate(
                label="injected",
                overrides={
                    "target_repo_id": "payments",
                    "harness_source": HarnessSource.injected,
                },
            ),
        ],
    }
    base.update(over)
    return ExperimentCreate(**base)


def test_an_experiment_without_a_question_is_a_batch(lab):
    with pytest.raises(ExperimentError, match="needs a question"):
        lab.create_experiment(_payload(question="  "))


def test_a_condition_cannot_fix_an_axis_that_does_not_exist(lab):
    with pytest.raises(ExperimentError, match="unknown axis"):
        lab.create_experiment(
            _payload(
                conditions=[
                    ExperimentConditionCreate(
                        label="only",
                        role=ConditionRole.control,
                        overrides={"temperature": 0.7},
                    )
                ]
            )
        )


def test_creating_the_canonical_harness_ab(lab):
    experiment, warnings = lab.create_experiment(_payload())

    assert experiment.status == ExperimentStatus.draft
    assert [c.label for c in lab.conditions("migration-safety")] == [
        "native",
        "injected",
    ]
    # Control is listed first: it is what the rest is read against.
    assert lab.conditions("migration-safety")[0].role == ConditionRole.control
    assert warnings == []  # 5 repetitions, one factor varied


def test_launching_expands_the_declared_design(lab):
    lab.create_experiment(_payload(repetitions=3))
    runs = lab.launch(
        "migration-safety",
        RunConfigCreate(
            case_id="add-endpoint",
            prompt_variant="default",
            executor_model="stub-model",
        ),
    )

    assert len(runs) == 6  # 1 case x 1 variant x 2 conditions x 3 repetitions
    assert {run.experiment_id for run in runs} == {"migration-safety"}
    labels = [run.condition_label for run in runs]
    assert labels.count("native") == 3
    assert labels.count("injected") == 3
    assert lab.get_experiment("migration-safety").status == ExperimentStatus.running


def test_every_run_of_an_arm_carries_the_declared_overrides(lab):
    lab.create_experiment(_payload(repetitions=1))
    runs = lab.launch(
        "migration-safety",
        RunConfigCreate(
            case_id="add-endpoint",
            prompt_variant="default",
            executor_model="stub-model",
        ),
    )
    service = RunService(lab.session)
    by_label = {
        run.condition_label: service.get_run_config(run.run_config_id) for run in runs
    }

    assert by_label["native"].harness_source == HarnessSource.native
    assert by_label["injected"].harness_source == HarnessSource.injected
    # Same repository, same commit: the harness is the only difference.
    assert by_label["native"].target_commit == by_label["injected"].target_commit
    # And the shared settings came from the launch payload, not the condition.
    assert by_label["native"].executor_model == "stub-model"
