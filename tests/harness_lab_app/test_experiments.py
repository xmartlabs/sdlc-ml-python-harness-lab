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
    RunState,
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


def test_subject_validation_needs_no_control():
    """Every arm is a peer. There is nothing for the others to be measured against."""
    assert (
        validate_design(
            ExperimentMode.subject_validation,
            [
                _condition("claude", harness="claude"),
                _condition("opencode", harness="opencode"),
            ],
        )
        == []
    )


def test_subject_validation_accepts_a_single_arm():
    """ "Does it work under my setup?" is a legitimate question with one answer."""
    assert (
        validate_design(
            ExperimentMode.subject_validation, [_condition("claude", harness="claude")]
        )
        == []
    )


def test_subject_validation_may_not_vary_the_repository():
    with pytest.raises(ExperimentDesignError, match="holds the repository fixed"):
        validate_design(
            ExperimentMode.subject_validation,
            [
                _condition("here", target_repo_id="a"),
                _condition("there", target_repo_id="b"),
            ],
        )


def test_subject_validation_may_not_ablate():
    """Ablation is the other mode's question; allowing it here smuggles it in."""
    with pytest.raises(ExperimentDesignError, match="does not ablate"):
        validate_design(
            ExperimentMode.subject_validation,
            [
                _condition("full", skills_disabled=[]),
                _condition("stripped", skills_disabled=["planning"]),
            ],
        )


def test_subject_validation_warns_when_harness_and_model_both_vary():
    """The cross product is the point — but each cell is its own population."""
    warnings = validate_design(
        ExperimentMode.subject_validation,
        [
            _condition("a", harness="claude", executor_model="x"),
            _condition("b", harness="opencode", executor_model="y"),
        ],
    )
    assert any("one <harness>/<model> arm at a time" in w for w in warnings)


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


# --------------------------------------------------------------------------
# Subject validation — the mode that tests a tool, not the harness (ADR-0040)
# --------------------------------------------------------------------------


def _subject_payload(**over) -> ExperimentCreate:
    base = {
        "experiment_id": "does-notion-work",
        "question": "Does the Notion skill save the page reliably?",
        "mode": ExperimentMode.subject_validation,
        "subject": "notion-export skill",
        "case_ids": ["save-to-txt"],
        "prompt_variants": ["default"],
        "repetitions": 3,
        "conditions": [
            ExperimentConditionCreate(label="claude", overrides={"harness": "claude"}),
            ExperimentConditionCreate(
                label="opencode", overrides={"harness": "opencode"}
            ),
        ],
    }
    base.update(over)
    return ExperimentCreate(**base)


@pytest.fixture
def validated_case(lab):
    """A case that declares what success is — the precondition of this mode."""
    CaseService(lab.session).create_case(
        CaseCreate(
            case_id="save-to-txt",
            name="Save the page to a txt",
            type=CaseType.repo_task,
            prompt_variants={"default": "Read the page and save it to notes.txt"},
            validation_commands=["test -s {workspace}/notes.txt"],
        )
    )
    return lab


def test_subject_validation_needs_a_named_subject(validated_case):
    with pytest.raises(ExperimentError, match="needs a subject"):
        validated_case.create_experiment(_subject_payload(subject="  "))


def test_subject_validation_refuses_a_case_that_never_says_what_success_is(lab):
    """The gate that keeps this mode from running blind.

    Without an executable command the runs still execute, produce no verdict,
    and the pass rate this mode exists to report is simply absent — with no
    error anywhere to explain why. Refusing at creation is the whole point.
    """
    CaseService(lab.session).create_case(
        CaseCreate(
            case_id="vibes-only",
            name="Vibes",
            type=CaseType.repo_task,
            prompt_variants={"default": "Do the thing"},
            # Prose, not a command: `build_validation_spec` drops it silently.
            validation_commands=["the file should look right"],
        )
    )
    with pytest.raises(ExperimentError, match="executable validation command"):
        lab.create_experiment(_subject_payload(case_ids=["vibes-only"]))


def test_subject_validation_creates_without_a_control(validated_case):
    experiment, warnings = validated_case.create_experiment(_subject_payload())

    assert experiment.subject == "notion-export skill"
    assert [c.role for c in validated_case.conditions("does-notion-work")] == [
        ConditionRole.treatment,
        ConditionRole.treatment,
    ]
    assert any("exploratory comparison" in w for w in warnings)


def test_subject_validation_summary_reports_per_arm_and_never_pools(validated_case):
    validated_case.create_experiment(_subject_payload())

    summary = validated_case.summarize("does-notion-work")

    assert summary["subject"] == "notion-export skill"
    # No control means no delta, whatever roles the rows carry.
    assert summary["comparisons"] == []
    assert summary["verdict"]["causal"] is False
    assert [c["label"] for c in summary["conditions"]] == ["claude", "opencode"]
    assert any(
        "says nothing about the harness" in c for c in summary["verdict"]["caveats"]
    )
    assert any("not pooled" in c for c in summary["verdict"]["caveats"])
    # The grid is counts per cell, so repetitions survive; nothing has run here.
    assert summary["case_outcomes"] == {"claude": {}, "opencode": {}}


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

    session.add(
        Case(
            case_id="tetris-v1",
            name="Tetris",
            type=CaseType.single_prompt,
            prompt_variants={"sdlc": "Build Tetris, following the harness."},
        )
    )
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

    session.add(
        Case(
            case_id="tetris-v1",
            name="Tetris",
            type=CaseType.single_prompt,
            prompt_variants={
                "naive": "Build Tetris.",
                "sdlc": "Build Tetris, following the harness.",
            },
        )
    )
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


def test_launch_refuses_a_prompt_variant_the_case_does_not_define(session):
    """An undefined variant means an empty prompt, and an empty prompt is a loss.

    The prompt lookup falls back to "" for a variant a case does not declare,
    so the run went out with nothing to do — inside one arm, which then read as
    that arm being bad. The refusal belongs at launch, before anything is
    persisted, exactly as it does for a batch (ADR-0023).
    """
    from harness_lab_app.models import Case, CaseType
    from harness_lab_app.schemas import RunConfigCreate

    session.add(
        Case(
            case_id="pokemon-v1",
            name="Pokemon",
            type=CaseType.single_prompt,
            prompt_variants={"default": "Build the game."},
        )
    )
    session.commit()
    service = ExperimentService(session)
    service.create_experiment(
        ExperimentCreate(
            experiment_id="mixed-families",
            name="Mixed families",
            question="Does the harness as a whole earn its keep?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["pokemon-v1"],
            repetitions=1,
            conditions=[
                ExperimentConditionCreate(
                    label="bare",
                    role=ConditionRole.control,
                    overrides={"prompt_variant": "default", "skills_disabled": ["x"]},
                ),
                ExperimentConditionCreate(
                    label="full-harness",
                    role=ConditionRole.treatment,
                    # A variant from another prompt family — this case has none.
                    overrides={"prompt_variant": "sdlc", "skills_disabled": []},
                ),
            ],
        )
    )

    with pytest.raises(ExperimentError, match="empty prompt"):
        service.launch(
            "mixed-families",
            RunConfigCreate(
                case_id="pokemon-v1",
                prompt_variant="default",
                executor_model="stub-executor",
                score_mode="manual",
                max_steps=5,
                dry_run=True,
            ),
        )

    # One intent: the arm that *was* runnable is not left half-launched either.
    assert service.runs("mixed-families") == []


def test_a_single_variant_case_can_still_carry_the_full_harness_pair(session):
    """Same prompt on both arms is a design, not a mistake.

    A case written in the UI declares one prompt, and that prompt names no
    governance for the bare arm to lose. The treatment is then the skill
    catalog — physically hidden on one side, available and asked for on the
    other — and both arms must expand and run.
    """
    from harness_lab_app.models import Case, CaseType
    from harness_lab_app.run_service import RunService
    from harness_lab_app.schemas import RunConfigCreate

    session.add(
        Case(
            case_id="pokemon-v1",
            name="Pokemon",
            type=CaseType.single_prompt,
            prompt_variants={"default": "Build the game."},
        )
    )
    session.commit()
    service = ExperimentService(session)
    service.create_experiment(
        ExperimentCreate(
            experiment_id="one-prompt",
            name="One prompt",
            question="Is the governed catalog worth anything on a plain prompt?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["pokemon-v1"],
            repetitions=1,
            conditions=[
                ExperimentConditionCreate(
                    label="bare",
                    role=ConditionRole.control,
                    overrides={
                        "prompt_variant": "default",
                        "skills_disabled": ["brainstorm_quick"],
                    },
                ),
                ExperimentConditionCreate(
                    label="full-harness",
                    role=ConditionRole.treatment,
                    overrides={
                        "prompt_variant": "default",
                        "skills_disabled": [],
                        # The activation block only rides the arm that can see
                        # the skill; preflight refuses it on the bare arm.
                        "skills_invoke": ["brainstorm_quick"],
                    },
                ),
            ],
        )
    )

    runs = service.launch(
        "one-prompt",
        RunConfigCreate(
            case_id="pokemon-v1",
            prompt_variant="default",
            executor_model="stub-executor",
            score_mode="manual",
            max_steps=5,
            dry_run=True,
        ),
    )

    run_service = RunService(session)
    arms = {}
    for run in runs:
        config = run_service.get_run_config(run.run_config_id)
        arms[run.condition_label] = config
    assert set(arms) == {"bare", "full-harness"}
    assert (
        arms["bare"].prompt_variant == arms["full-harness"].prompt_variant == "default"
    )
    assert arms["bare"].skills_disabled == ["brainstorm_quick"]
    assert arms["bare"].skills_invoke == []
    assert arms["full-harness"].skills_disabled == []
    assert arms["full-harness"].skills_invoke == ["brainstorm_quick"]


def _skills_design(service, experiment_id="lockcheck"):
    from sqlmodel import select

    from harness_lab_app.models import Case, CaseType

    session = service.session
    if not session.exec(select(Case).where(Case.case_id == "tetris-v1")).first():
        session.add(
            Case(
                case_id="tetris-v1",
                name="Tetris",
                type=CaseType.single_prompt,
                prompt_variants={"sdlc": "Build Tetris, following the harness."},
            )
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


def test_abandon_hides_the_experiment_but_keeps_its_runs(session):
    """Abandonment is 'stop showing me this', never 'this never happened'."""
    from sqlmodel import select

    from harness_lab_app.models import Run

    service = ExperimentService(session)
    _skills_design(service, "old-junk")
    service.launch("old-junk", _base_config())

    abandoned = service.abandon_experiment("old-junk")

    assert abandoned.status == ExperimentStatus.abandoned
    assert len(list(session.exec(select(Run)).all())) == 2


def test_deleting_an_experiment_with_runs_needs_the_explicit_flag(session):
    """Half a deletion is worse than none: orphan runs would bias every board."""
    service = ExperimentService(session)
    _skills_design(service, "half-delete")
    service.launch("half-delete", _base_config())
    for run in service.runs("half-delete"):
        run.state = RunState.completed
        service.session.add(run)
    service.session.commit()

    with pytest.raises(ExperimentError, match="delete_runs=true"):
        service.delete_experiment("half-delete")


def test_deleting_with_runs_erases_the_whole_measurement(session):
    """Experiment, conditions, runs, configs AND scores — nothing left to bias."""
    from sqlmodel import select

    from harness_lab_app.models import (
        Experiment,
        ExperimentCondition,
        Run,
        RunConfig,
        Score,
    )

    service = ExperimentService(session)
    _skills_design(service, "junk")
    runs = service.launch("junk", _base_config())
    for run in runs:
        run.state = RunState.completed
        session.add(run)
    session.add(Score(run_id=runs[0].run_id, name="cost_usd", version=1, value_num=1.0))
    session.commit()

    result = service.delete_experiment("junk", delete_runs=True)

    assert result["deleted_runs"] == 2
    for model in (Run, RunConfig, Score, ExperimentCondition):
        assert list(session.exec(select(model)).all()) == []
    assert (
        session.exec(
            select(Experiment).where(Experiment.experiment_id == "junk")
        ).first()
        is None
    )


def test_a_live_experiment_cannot_be_deleted_mid_flight(session):
    """Pending or running arms must be cancelled before the record disappears."""
    service = ExperimentService(session)
    _skills_design(service, "live")
    service.launch("live", _base_config())  # runs persist as pending

    with pytest.raises(ExperimentError, match="Cancel them first"):
        service.delete_experiment("live", delete_runs=True)


def test_a_caseless_draft_deletes_cleanly(session):
    """The 'audit with no cases' fossil: a draft with nothing in it just goes."""
    service = ExperimentService(session)
    service.create_experiment(
        ExperimentCreate(
            experiment_id="empty-draft",
            name="Empty draft",
            question="How does this repo behave under an agent?",
            mode=ExperimentMode.discovery,
            case_ids=[],
            repetitions=2,
            conditions=[
                ExperimentConditionCreate(
                    label="as-is",
                    role=ConditionRole.treatment,
                    overrides={"target_repo_id": "somewhere"},
                )
            ],
        )
    )

    result = service.delete_experiment("empty-draft")

    assert result["deleted_runs"] == 0


# --------------------------------------------------------------------------
# Validity — an experiment says whether it could answer, before it answers
# (ADR-0046)
# --------------------------------------------------------------------------


def _staged_skill_design(service, experiment_id="activation"):
    from sqlmodel import select

    from harness_lab_app.models import Case, CaseType, StagedSkill

    session = service.session
    if not session.exec(select(Case).where(Case.case_id == "tetris-v1")).first():
        session.add(
            Case(
                case_id="tetris-v1",
                name="Tetris",
                type=CaseType.single_prompt,
                prompt_variants={"sdlc": "Build Tetris, following the harness."},
            )
        )
    if not session.exec(
        select(StagedSkill).where(StagedSkill.name == "brainstorm_quick")
    ).first():
        session.add(StagedSkill(name="brainstorm_quick", status="testing"))
    session.commit()
    return service.create_experiment(
        ExperimentCreate(
            experiment_id=experiment_id,
            name="Activation",
            question="Does brainstorm_quick help?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["tetris-v1"],
            repetitions=2,
            conditions=[
                ExperimentConditionCreate(
                    label="baseline", role=ConditionRole.control, overrides={}
                ),
                ExperimentConditionCreate(
                    label="with-skills",
                    role=ConditionRole.treatment,
                    overrides={
                        "skills_extra": ["brainstorm_quick"],
                        "skills_invoke": ["brainstorm_quick"],
                    },
                ),
            ],
        )
    )


def _score(session, run, *, passed, skill_invoked=None, stop_reason=None):
    """Give a launched run the artifacts a real one would leave behind."""
    from harness_lab_app.models import RunState, Score, Validation

    run.state = RunState.completed
    session.add(run)
    session.add(
        Score(
            run_id=run.run_id, name="task_validation_pass", version=1, value_bool=passed
        )
    )
    if skill_invoked is not None:
        session.add(
            Score(
                run_id=run.run_id,
                name="skill_invoked",
                version=1,
                value_num=skill_invoked,
            )
        )
    if stop_reason:
        session.add(Validation(run_id=run.run_id, effort={"stop_reason": stop_reason}))
    session.commit()


def test_a_treatment_that_never_invoked_its_skill_invalidates_the_summary(session):
    """The headline used to read as a result. The measurement stays; it stops leading."""
    service = ExperimentService(session)
    _staged_skill_design(service)
    runs = service.launch("activation", _base_config())
    for run in runs:
        treatment = run.condition_label == "with-skills"
        _score(
            session,
            run,
            passed=treatment,
            skill_invoked=0.0 if treatment else None,
        )

    summary = service.summarize("activation")

    assert summary["validity"]["status"] == "invalid"
    # Invalid leads, degraded follows.
    assert [issue["code"] for issue in summary["validity"]["issues"]] == [
        "treatment_not_activated",
        "design_objection",
    ]
    assert summary["verdict"]["headline"] == "Comparison not valid"
    assert summary["verdict"]["tone"] == "invalid"
    # The numbers are still there — they are just no longer the first thing read.
    assert "+100 points" in summary["verdict"]["observed"]


def test_runs_that_died_on_the_connection_are_flagged_not_dropped(session):
    """A harness_error is infrastructure. Filtering it would read as 'no evidence yet'."""
    from harness_lab_app.models import RunState

    service = ExperimentService(session)
    _skills_design(service, "crashed")
    runs = service.launch("crashed", _base_config())
    for run in runs:
        _score(session, run, passed=False, stop_reason="harness_error")
        run.state = RunState.failed
        session.add(run)
    session.commit()

    summary = service.summarize("crashed")

    codes = [issue["code"] for issue in summary["validity"]["issues"]]
    assert "infrastructure_failures" in codes
    assert summary["validity"]["status"] == "invalid"
    # Flagged, not filtered: the runs are still counted where cost is read.
    assert all(condition["runs"] == 1 for condition in summary["conditions"])


def test_a_skill_unopened_by_a_run_that_crashed_is_not_evidence(session):
    """It never got the chance. Counting it would blame the skill for the network."""
    from harness_lab_app.models import RunState

    service = ExperimentService(session)
    _staged_skill_design(service, "half-crashed")
    runs = service.launch("half-crashed", _base_config())
    for run in runs:
        treatment = run.condition_label == "with-skills"
        _score(
            session, run, passed=not treatment, skill_invoked=0.0 if treatment else None
        )
        if treatment:
            run.state = RunState.failed
            session.add(run)
    session.commit()

    summary = service.summarize("half-crashed")

    codes = [issue["code"] for issue in summary["validity"]["issues"]]
    assert "treatment_not_activated" not in codes


def test_a_design_the_wizard_objected_to_keeps_the_objection(session):
    """The 201 body is not a place a warning can survive to be read again."""
    service = ExperimentService(session)
    experiment, warnings = _staged_skill_design(service, "objected")

    assert any("only the skill directories differ" in note for note in warnings)
    assert experiment.design_notes == warnings[:1]

    runs = service.launch("objected", _base_config())
    for run in runs:
        _score(
            session, run, passed=run.condition_label == "with-skills", skill_invoked=1.0
        )

    summary = service.summarize("objected")
    codes = [issue["code"] for issue in summary["validity"]["issues"]]
    assert "design_objection" in codes


def test_a_full_harness_arm_is_about_the_catalog_its_control_removes(session):
    """The treatment stages nothing, so its skills live on the control's ablation.

    Three experiments in this lab reported a confident verdict while all six
    full-harness runs carried `skill_invoked = 0`. Reading each arm's own
    overrides left the treatment with an empty skill list, and the observation
    had nowhere to land. The scorer already unions the siblings'
    `skills_disabled` (`score_service.build_context`); the summary now reads the
    arms the same way it scored them.
    """
    from harness_lab_app.models import Case, CaseType
    from harness_lab_app.skills_catalog import known_skill_names

    governed = sorted(known_skill_names())[:4]
    if len(governed) < 4:
        pytest.skip("not enough governed skills in this checkout")
    service = ExperimentService(session)
    session.add(
        Case(
            case_id="tetris-v1",
            name="Tetris",
            type=CaseType.single_prompt,
            prompt_variants={"sdlc": "Build Tetris."},
        )
    )
    session.commit()
    service.create_experiment(
        ExperimentCreate(
            experiment_id="full-harness",
            name="Full harness",
            question="Does the full harness help?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["tetris-v1"],
            repetitions=2,
            conditions=[
                ExperimentConditionCreate(
                    label="bare",
                    role=ConditionRole.control,
                    overrides={"skills_disabled": governed},
                ),
                ExperimentConditionCreate(
                    label="full-harness",
                    role=ConditionRole.treatment,
                    overrides={"skills_disabled": []},
                ),
            ],
        )
    )
    runs = service.launch("full-harness", _base_config())
    for run in runs:
        treatment = run.condition_label == "full-harness"
        _score(session, run, passed=True, skill_invoked=0.0 if treatment else None)

    summary = service.summarize("full-harness")

    issue = next(
        item
        for item in summary["validity"]["issues"]
        if item["code"] == "treatment_not_activated"
    )
    assert issue["arm"] == "full-harness"
    # Four skills is past the point where naming each one helps a reader.
    assert "none of its 4 skills" in issue["detail"]
    # The bare arm removed them, so it is not the arm that failed to open them.
    assert summary["validity"]["status"] == "invalid"
