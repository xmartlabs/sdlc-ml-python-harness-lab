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


# --------------------------------------------------------------------------
# Reading the result — the mode governs what may be claimed (§10, §25)
# --------------------------------------------------------------------------


def test_discovery_summary_carries_no_comparison_and_no_causal_verdict(lab):
    """Mode C describes a repository. It concludes nothing about a change."""
    lab.create_experiment(
        _payload(
            experiment_id="client-discovery",
            mode=ExperimentMode.discovery,
            repetitions=1,
            conditions=[
                ExperimentConditionCreate(
                    label="as-is",
                    # A discovery arm may carry the control role and still must
                    # not be rendered as one half of a comparison.
                    role=ConditionRole.control,
                    overrides={"target_repo_id": "payments"},
                ),
                ExperimentConditionCreate(
                    label="also-as-is",
                    overrides={"target_repo_id": "payments"},
                ),
            ],
        )
    )

    summary = lab.summarize("client-discovery")

    assert summary["comparisons"] == []
    assert summary["verdict"]["causal"] is False
    assert summary["verdict"]["headline"] == "Exploratory — no comparison"


def test_cross_repo_summary_keeps_repositories_apart(lab):
    """Mode B reports per repository; it never pools the arms into one score."""
    TargetService(lab.session).create_target(
        TargetRepositoryCreate(
            target_id="billing",
            name="Billing",
            repository_url="https://github.com/acme/billing.git",
        )
    )
    lab.create_experiment(
        _payload(
            experiment_id="does-it-travel",
            mode=ExperimentMode.cross_repo,
            conditions=[
                ExperimentConditionCreate(
                    label="payments",
                    role=ConditionRole.control,
                    overrides={"target_repo_id": "payments"},
                ),
                ExperimentConditionCreate(
                    label="billing",
                    overrides={"target_repo_id": "billing"},
                ),
            ],
        )
    )

    summary = lab.summarize("does-it-travel")

    # Every repository keeps its own row; there is no pooled aggregate anywhere
    # in the payload for a reader to mistake for a result.
    assert [c["label"] for c in summary["conditions"]] == ["payments", "billing"]
    assert summary["verdict"]["causal"] is False
    assert any("not pooled" in c for c in summary["verdict"]["caveats"])


def test_a_harness_summary_reports_its_evidence_honestly(lab):
    lab.create_experiment(_payload(repetitions=2))

    summary = lab.summarize("migration-safety")

    assert summary["verdict"]["evidence"] == "1 case \u00d7 2 repetitions"
    assert summary["verdict"]["strength"] == "exploratory comparison"
    # Nothing has run, so there is nothing to conclude.
    assert summary["verdict"]["headline"] == "Not enough evidence yet"


def test_the_summary_reports_per_case_repetitions_from_a_suite(lab):
    lab.create_experiment(
        _payload(repetitions=5, case_repetitions={"add-endpoint": 1}, suite_id="s1")
    )

    summary = lab.summarize("migration-safety")

    assert summary["suite_id"] == "s1"
    assert summary["case_repetitions"] == {"add-endpoint": 1}
    # The weakest case governs the wording, not the headline number.
    assert summary["verdict"]["strength"] == "exploratory"


def test_launch_preflights_every_arm_not_just_the_baseline(session):
    """A treatment differing only in skills must still be preflighted.

    The launch dedupe key once omitted the skill axes — the very fields a
    Mode A experiment varies — so the treatment config collapsed onto its
    baseline and skipped preflight. An unknown staged skill then failed at run
    time, inside one arm only, which is indistinguishable from the treatment
    being bad. The refusal has to come at launch, for the whole experiment.
    """
    from harness_lab_app.models import Case, CaseType
    from harness_lab_app.run_service import RunPreflightError
    from harness_lab_app.schemas import RunConfigCreate

    session.add(Case(case_id="tetris-v1", name="Tetris", type=CaseType.single_prompt))
    session.commit()
    service = ExperimentService(session)
    service.create_experiment(
        ExperimentCreate(
            experiment_id="ghost-skill",
            name="Ghost skill",
            question="Does a skill nobody staged help?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["tetris-v1"],
            repetitions=2,
            conditions=[
                ExperimentConditionCreate(
                    label="baseline",
                    role=ConditionRole.control,
                    overrides={"skills_extra": []},
                ),
                ExperimentConditionCreate(
                    label="with-skill",
                    role=ConditionRole.treatment,
                    overrides={"skills_extra": ["ghost-skill-nobody-staged"]},
                ),
            ],
        )
    )

    with pytest.raises(RunPreflightError, match="ghost-skill-nobody-staged"):
        service.launch(
            "ghost-skill",
            RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                executor_model="stub-executor",
                score_mode="manual",
                max_steps=5,
            ),
        )

    # Refused as one intent: no half-launched experiment left behind.
    assert service.runs("ghost-skill") == []


def test_full_harness_design_expands_per_arm_prompt_and_skills(session):
    """Bare model versus the whole harness — the lab's founding comparison.

    One treatment, two levers moved together on purpose: the governance-loaded
    prompt variant and the full skill catalog. The claim is about the harness
    as a unit, so this must be one accepted design — and each arm's runs must
    carry that arm's own prompt and ablation, or the two arms silently run the
    same condition.
    """
    from harness_lab_app.models import Case, CaseType
    from harness_lab_app.run_service import RunService
    from harness_lab_app.schemas import RunConfigCreate

    session.add(Case(case_id="tetris-v1", name="Tetris", type=CaseType.single_prompt))
    session.commit()
    service = ExperimentService(session)
    service.create_experiment(
        ExperimentCreate(
            experiment_id="full-harness",
            name="Full harness",
            question="Does the harness as a whole earn its keep?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["tetris-v1"],
            repetitions=2,
            conditions=[
                ExperimentConditionCreate(
                    label="bare",
                    role=ConditionRole.control,
                    overrides={
                        "prompt_variant": "naive",
                        "skills_disabled": ["brainstorm_quick", "bootstrap_project"],
                    },
                ),
                ExperimentConditionCreate(
                    label="full-harness",
                    role=ConditionRole.treatment,
                    overrides={"prompt_variant": "sdlc", "skills_disabled": []},
                ),
            ],
        )
    )

    runs = service.launch(
        "full-harness",
        RunConfigCreate(
            case_id="tetris-v1",
            prompt_variant="sdlc",
            executor_model="stub-executor",
            score_mode="manual",
            max_steps=5,
            dry_run=True,
        ),
    )

    run_service = RunService(session)
    by_arm = {}
    for run in runs:
        config = run_service.get_run_config(run.run_config_id)
        by_arm.setdefault(
            run.condition_label, (config.prompt_variant, sorted(config.skills_disabled))
        )
    assert by_arm["bare"] == ("naive", ["bootstrap_project", "brainstorm_quick"])
    assert by_arm["full-harness"] == ("sdlc", [])


def test_prompt_variant_alone_is_a_valid_harness_axis():
    """naive-vs-sdlc with nothing else varying is the classic A/B, not a refusal."""
    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            {
                "label": "bare",
                "role": ConditionRole.control,
                "overrides": {"prompt_variant": "naive"},
            },
            {
                "label": "governed",
                "role": ConditionRole.treatment,
                "overrides": {"prompt_variant": "sdlc"},
            },
        ],
    )
    assert warnings == []


def _skills_design(service, experiment_id="lockcheck"):
    from sqlmodel import select

    from harness_lab_app.models import Case, CaseType

    session = service.session
    if not session.exec(select(Case).where(Case.case_id == "tetris-v1")).first():
        session.add(
            Case(case_id="tetris-v1", name="Tetris", type=CaseType.single_prompt)
        )
        session.commit()
    service.create_experiment(
        ExperimentCreate(
            experiment_id=experiment_id,
            name="Lock check",
            question="Does ablating brainstorm_quick change anything?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["tetris-v1"],
            repetitions=1,
            conditions=[
                ExperimentConditionCreate(
                    label="baseline", role=ConditionRole.control, overrides={}
                ),
                ExperimentConditionCreate(
                    label="ablated",
                    role=ConditionRole.treatment,
                    overrides={"skills_disabled": ["brainstorm_quick"]},
                ),
            ],
        )
    )


def _base_config(**over):
    from harness_lab_app.schemas import RunConfigCreate

    payload = {
        "case_id": "tetris-v1",
        "prompt_variant": "sdlc",
        "executor_model": "stub-executor",
        "score_mode": "manual",
        "max_steps": 5,
    }
    payload.update(over)
    return RunConfigCreate(**payload)


def test_relaunching_with_a_different_model_is_refused(session):
    """One experiment, one measurement configuration.

    A relaunch under another model would file two measurements under the same
    experiment_id, and every aggregate would average them as though they were
    one condition. Same-config relaunches stay allowed: that is how
    repetitions are added.
    """
    service = ExperimentService(session)
    _skills_design(service)
    service.launch("lockcheck", _base_config())

    with pytest.raises(ExperimentError, match="executor_model"):
        service.launch("lockcheck", _base_config(executor_model="other-model"))

    # The same configuration adds repetitions instead.
    again = service.launch("lockcheck", _base_config())
    assert len(again) == 2
    assert len(service.runs("lockcheck")) == 4


def test_a_dry_run_rehearsal_neither_locks_nor_is_blocked(session):
    """Dry runs build commands; they measure nothing, so they lock nothing."""
    service = ExperimentService(session)
    _skills_design(service, "rehearsal")
    service.launch("rehearsal", _base_config(dry_run=True))

    # A real launch under a different model is fine after a dry rehearsal…
    service.launch("rehearsal", _base_config(executor_model="the-real-model"))
    # …and a later dry run under yet another model is never blocked.
    service.launch("rehearsal", _base_config(executor_model="whatever", dry_run=True))


def test_launch_config_reports_what_real_runs_carry(session):
    """The detail payload's launch_config: None for drafts, locked afterwards."""
    service = ExperimentService(session)
    _skills_design(service, "cfg")
    assert service.launch_config("cfg") is None

    service.launch("cfg", _base_config(dry_run=True))
    assert service.launch_config("cfg") is None, "a rehearsal is not a measurement"

    service.launch("cfg", _base_config())
    config = service.launch_config("cfg")
    assert config is not None
    assert config["executor_model"] == "stub-executor"
    assert config["prompt_variant"] == "sdlc"
    assert config["max_steps"] == 5


def test_an_experiment_without_cases_warns_at_creation(session):
    """'Has no cases' must be said at creation, not three screens later at launch."""
    service = ExperimentService(session)
    _, warnings = service.create_experiment(
        ExperimentCreate(
            experiment_id="empty-audit",
            name="Empty audit",
            question="How does this repo behave under an agent?",
            mode=ExperimentMode.discovery,
            case_ids=[],
            repetitions=2,
            conditions=[
                ExperimentConditionCreate(
                    label="as-is",
                    role=ConditionRole.treatment,
                    overrides={"target_repo_id": "some-repo"},
                )
            ],
        )
    )
    assert any("no cases" in warning for warning in warnings)


def test_launch_enqueues_every_run_it_persists(session):
    """A launched run must reach the queue, not just the database.

    RunService._publish() silently no-ops when no enqueue callback was
    injected, and the launch path used to build its RunService without one —
    so an entire experiment sat `pending` forever with a healthy worker and no
    error anywhere. The API layer injects the publisher; this pins that the
    launch path actually hands it through.
    """
    published: list[str] = []
    service = ExperimentService(session, enqueue=published.append)
    _skills_design(service, "queued")

    runs = service.launch("queued", _base_config(dry_run=True))

    assert len(runs) == 2
    assert sorted(published) == sorted(run.run_id for run in runs)
