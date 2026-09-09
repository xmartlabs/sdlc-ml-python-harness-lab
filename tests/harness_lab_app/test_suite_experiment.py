"""The regression suite produces an experiment — it does not run one itself.

The point of §13: validating a harness release goes through the same design
validation, the same arm expansion and the same case matrix as every other
comparison, so `fixed / broken / unchanged` means the same thing here as it does
anywhere else in the lab.
"""

from __future__ import annotations

import pytest

from harness_lab_app.experiment_service import ExperimentService
from harness_lab_app.harness_lifecycle.models import (
    SuiteCase,
    SuiteComparison,
    SuiteDefinition,
)
from harness_lab_app.harness_lifecycle.suite_experiment import (
    SuiteArm,
    SuiteExperimentError,
    experiment_from_suite,
)
from harness_lab_app.models import ConditionRole, ExperimentMode

INSTALLED = SuiteArm("installed", {"skills_extra": []})
CANDIDATE = SuiteArm("candidate-v0.7", {"skills_extra": ["writing-plans-v2"]})


def suite(**overrides) -> SuiteDefinition:
    base = {
        "suite_id": "harness-regression-v1",
        "cases": [
            SuiteCase(id="cli-textstats-v1", role="simple-feature", repetitions=3),
            SuiteCase(id="pallets__flask-4992", role="swebench-easy", repetitions=1),
        ],
        "comparison": SuiteComparison(
            controlled_fields=["case", "prompt", "executor_model", "max_steps"]
        ),
    }
    base.update(overrides)
    return SuiteDefinition(**base)


def test_the_suite_supplies_the_cases_and_their_repetitions():
    payload = experiment_from_suite(
        suite(), experiment_id="reg-1", control=INSTALLED, treatment=CANDIDATE
    )

    assert payload.case_ids == ["cli-textstats-v1", "pallets__flask-4992"]
    # The suite repeats a cheap canary more than an expensive instance on
    # purpose; flattening that to one number would misrepresent both.
    assert payload.case_repetitions == {
        "cli-textstats-v1": 3,
        "pallets__flask-4992": 1,
    }
    assert payload.suite_id == "harness-regression-v1"


def test_the_headline_repetition_count_is_the_weakest_case():
    payload = experiment_from_suite(
        suite(), experiment_id="reg-1", control=INSTALLED, treatment=CANDIDATE
    )
    assert payload.repetitions == 1


def test_it_is_an_ordinary_harness_evaluation_with_one_control():
    payload = experiment_from_suite(
        suite(), experiment_id="reg-1", control=INSTALLED, treatment=CANDIDATE
    )

    assert payload.mode == ExperimentMode.harness_evaluation
    roles = [(c.label, c.role) for c in payload.conditions]
    assert roles == [
        ("installed", ConditionRole.control),
        ("candidate-v0.7", ConditionRole.treatment),
    ]


def test_an_arm_that_varies_a_controlled_field_is_refused():
    with pytest.raises(SuiteExperimentError, match="controls executor_model"):
        experiment_from_suite(
            suite(),
            experiment_id="reg-1",
            control=SuiteArm("a", {"executor_model": "m1"}),
            treatment=SuiteArm("b", {"executor_model": "m2"}),
        )


def test_two_identical_arms_are_refused():
    with pytest.raises(SuiteExperimentError, match="same axis values"):
        experiment_from_suite(
            suite(),
            experiment_id="reg-1",
            control=SuiteArm("a", {"skills_extra": []}),
            treatment=SuiteArm("b", {"skills_extra": []}),
        )


def test_a_suite_with_no_cases_measures_nothing():
    with pytest.raises(SuiteExperimentError, match="no cases"):
        experiment_from_suite(
            suite(cases=[]),
            experiment_id="reg-1",
            control=INSTALLED,
            treatment=CANDIDATE,
        )


def test_the_generated_experiment_survives_the_ordinary_creation_path(session):
    """No bespoke path: it is created and validated like any other experiment."""
    payload = experiment_from_suite(
        suite(), experiment_id="reg-1", control=INSTALLED, treatment=CANDIDATE
    )

    experiment, warnings = ExperimentService(session).create_experiment(payload)

    assert experiment.suite_id == "harness-regression-v1"
    assert experiment.repetitions_for("cli-textstats-v1") == 3
    assert experiment.repetitions_for("pallets__flask-4992") == 1
    # The caveat is stated for the weakest case, not hidden by the strongest.
    assert any("exploratory only" in warning for warning in warnings)


def test_the_real_repository_suite_translates(tmp_path):
    """The shipped suite.yaml is a valid producer, not just the fixture."""
    from pathlib import Path

    from harness_lab_app.harness_lifecycle.suite import load_default_suite

    payload = experiment_from_suite(
        load_default_suite(Path(__file__).resolve().parents[2]),
        experiment_id="reg-real",
        control=INSTALLED,
        treatment=CANDIDATE,
    )
    assert len(payload.case_ids) == 2
    assert payload.case_repetitions["tetris-v2"] == 3


# --------------------------------------------------------------------------
# Over the API
# --------------------------------------------------------------------------


def _endpoint(path: str, method: str):
    from harness_lab_app.api import create_app

    for route in create_app().router.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {path} ({method})")


def _suite_request(**overrides):
    from harness_lab_app.schemas import SuiteArmCreate, SuiteExperimentCreate

    base = {
        "experiment_id": "harness-v07",
        "control": SuiteArmCreate(label="installed"),
        "treatment": SuiteArmCreate(
            label="candidate-v0.7", overrides={"skills_extra": ["writing-plans-v2"]}
        ),
    }
    base.update(overrides)
    return SuiteExperimentCreate(**base)


def test_the_endpoint_creates_the_experiment_from_the_shipped_suite(session):
    result = _endpoint("/api/experiments/from-suite", "POST")(
        _suite_request(), session=session
    )

    assert result["experiment_id"] == "harness-v07"
    assert result["suite_id"] == "harness-regression-v1"
    assert result["mode"] == ExperimentMode.harness_evaluation
    # Cases and per-case repetitions come from suite.yaml, not from the request.
    assert len(result["case_ids"]) == 2
    assert result["case_repetitions"]["tetris-v2"] == 3


def test_the_endpoint_refuses_an_unknown_suite(session):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        _endpoint("/api/experiments/from-suite", "POST")(
            _suite_request(suite_id="no-such-suite"), session=session
        )

    assert error.value.status_code == 404


def test_the_endpoint_refuses_arms_that_measure_nothing(session):
    from fastapi import HTTPException

    from harness_lab_app.schemas import SuiteArmCreate

    with pytest.raises(HTTPException) as error:
        _endpoint("/api/experiments/from-suite", "POST")(
            _suite_request(treatment=SuiteArmCreate(label="also-installed")),
            session=session,
        )

    assert error.value.status_code == 422
