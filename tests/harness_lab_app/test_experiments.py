"""Experiments: a declared question and a design the mode can actually answer.

The tests that matter here are the *rejections*. An experiment that varies two
factors at once is a valid request and an invalid measurement, and the whole
point of the abstraction is that the lab says so at creation instead of
rendering the average later (ADR-0028, ADR-0033).
"""

from __future__ import annotations

import pytest

from harness_lab_app.case_service import CaseService, CaseValidationError
from harness_lab_app.experiment_service import (
    ExperimentDesignError,
    ExperimentError,
    ExperimentNotFoundError,
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
                skills_disabled=["bootstrap_project"],
            ),
        ],
    )
    assert any("more than one harness factor" in w.lower() for w in warnings)


def test_a_bare_control_is_one_axis_not_four():
    """Bare-versus-full-harness is a single intervention (ADR-0049).

    A bare arm has no prompt family and no catalog to hold fixed — the runner
    masks every governance directory — so what its `prompt_variant` and
    `skills_disabled` say are declarations that keep the condition hash honest,
    not levers that moved. Reporting them as confounds would put a permanent
    `design_objection` on the lab's most basic comparison.
    """
    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            _condition(
                "bare",
                ConditionRole.control,
                harness_source=HarnessSource.bare,
                prompt_variant="default",
                skills_disabled=["bootstrap_project", "writing-plans"],
            ),
            _condition(
                "full-harness",
                prompt_variant="harness",
                skills_disabled=[],
            ),
        ],
    )

    assert warnings == []


def test_a_governance_only_pair_is_also_one_axis():
    """Same prompt, no skills on either side: what moved is the rulebook."""
    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            _condition(
                "bare",
                ConditionRole.control,
                harness_source=HarnessSource.bare,
                skills_disabled=["bootstrap_project"],
            ),
            _condition("governance-only", skills_disabled=["bootstrap_project"]),
        ],
    )

    assert warnings == []


def test_a_bare_arm_told_to_follow_a_harness_it_cannot_see_is_flagged():
    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            _condition(
                "bare",
                ConditionRole.control,
                harness_source=HarnessSource.bare,
                prompt_variant="harness",
            ),
            _condition("full-harness", prompt_variant="harness"),
        ],
    )

    assert any("cannot see" in warning for warning in warnings)


def test_the_partial_ablation_warning_stops_firing_once_an_arm_is_bare():
    """The advisory ADR-0046 added was true only while every arm was lab_root."""
    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            _condition(
                "bare",
                ConditionRole.control,
                harness_source=HarnessSource.bare,
                skills_disabled=["bootstrap_project"],
            ),
            _condition("governed", skills_disabled=[]),
        ],
    )

    assert not any("lab's own root" in warning for warning in warnings)


def test_the_governed_arms_keep_their_own_axes_beside_a_bare_one():
    """Subsumption covers the bare arm, not a real confound between the others."""
    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            _condition(
                "bare",
                ConditionRole.control,
                harness_source=HarnessSource.bare,
                prompt_variant="default",
            ),
            _condition("plain prompt", prompt_variant="default", skills_disabled=[]),
            _condition(
                "harness prompt",
                prompt_variant="harness",
                skills_disabled=["bootstrap_project"],
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
    [(1, "exploratory only"), (3, "a judgement"), (5, None), (10, None)],
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
    assert summary["verdict"]["strength"] == "judgement"
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
            scoring_rubric="10 when notes.txt holds the page, 0 when it is empty.",
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
    error anywhere to explain why. Refusing at creation is the whole point, and
    the case editor now refuses it one layer earlier still: a prose "command"
    never reaches the database, so the experiment gate is the second line of
    defence rather than the first.
    """
    with pytest.raises(CaseValidationError, match="runnable command"):
        CaseService(lab.session).create_case(
            CaseCreate(
                case_id="vibes-only",
                name="Vibes",
                type=CaseType.repo_task,
                prompt_variants={"default": "Do the thing"},
                # Prose, not a command: `build_validation_spec` used to drop it
                # silently, and the run reported nothing about it either way.
                validation_commands=["the file should look right"],
                scoring_rubric="10 when it looks right.",
            )
        )


def test_subject_validation_creates_without_a_control(validated_case):
    experiment, warnings = validated_case.create_experiment(_subject_payload())

    assert experiment.subject == "notion-export skill"
    assert [c.role for c in validated_case.conditions("does-notion-work")] == [
        ConditionRole.treatment,
        ConditionRole.treatment,
    ]
    assert any("a judgement" in w for w in warnings)


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


def test_creation_refuses_a_prompt_variant_the_case_does_not_define(session):
    """An undefined variant means an empty prompt, and an empty prompt is a loss.

    The prompt lookup falls back to "" for a variant a case does not declare,
    so the run went out with nothing to do — inside one arm, which then read as
    that arm being bad.

    Refusing at launch was not early enough: the experiment was already
    persisted, so a design that can never run left a row behind and burnt the
    id derived from its question — the next attempt at the same question had to
    be filed under a suffixed one. Which variant each arm fixes is knowable at
    creation, so that is where a mixed-family selection is refused.
    """
    from harness_lab_app.models import Case, CaseType

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
    design = ExperimentCreate(
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

    with pytest.raises(ExperimentError, match="empty prompt") as refusal:
        service.create_experiment(design)

    # The case and the arm are named: with several cases selected from two
    # prompt families, "some variant is missing" is not a fixable message.
    assert "case 'pokemon-v1' / arm 'full-harness'" in str(refusal.value)
    # Nothing was persisted, so the id derived from the question is still free.
    with pytest.raises(ExperimentNotFoundError):
        service.get_experiment("mixed-families")


def test_launch_still_refuses_a_variant_only_the_payload_names(session):
    """Creation cannot see every variant, so launch keeps the same guard.

    An arm that fixes no ``prompt_variant`` inherits the one the launch payload
    carries, and that payload does not exist at creation time. The refusal has
    to survive there too — with nothing half-launched, exactly as a batch owes
    (ADR-0023).
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
            experiment_id="payload-variant",
            name="Payload variant",
            question="Does the harness as a whole earn its keep?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["pokemon-v1"],
            repetitions=1,
            conditions=[
                ExperimentConditionCreate(
                    label="bare",
                    role=ConditionRole.control,
                    overrides={"skills_disabled": ["x"]},
                ),
                ExperimentConditionCreate(
                    label="full-harness",
                    role=ConditionRole.treatment,
                    overrides={"skills_disabled": []},
                ),
            ],
        )
    )

    with pytest.raises(ExperimentError, match="empty prompt"):
        service.launch(
            "payload-variant",
            RunConfigCreate(
                case_id="pokemon-v1",
                # A variant from another prompt family — this case has none.
                prompt_variant="sdlc",
                executor_model="stub-executor",
                score_mode="manual",
                max_steps=5,
                dry_run=True,
            ),
        )

    # One intent: the arm that *was* runnable is not left half-launched either.
    assert service.runs("payload-variant") == []


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


def _score(
    session, run, *, passed, skill_invoked=None, stop_reason=None, objective=None
):
    """Give a launched run the artifacts a real one would leave behind.

    ``passed=None`` writes no ``task_validation_pass`` at all, mirroring the
    evaluator: an objective that settled nothing is not a zero (ADR-0048).
    """
    from harness_lab_app.models import RunState, Score, Validation

    run.state = RunState.completed
    session.add(run)
    if passed is not None:
        session.add(
            Score(
                run_id=run.run_id,
                name="task_validation_pass",
                version=1,
                value_bool=passed,
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
    if stop_reason or objective is not None:
        session.add(
            Validation(
                run_id=run.run_id,
                effort={"stop_reason": stop_reason} if stop_reason else {},
                objective=objective or {},
            )
        )
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


def test_a_bare_arm_is_never_about_a_skill(session):
    """A bare arm sees no catalog, whatever its overrides happen to say.

    The sibling-removes rule is what gives a full-harness treatment its skill
    list, and it hands the same list back to the control. That is right when the
    control merely hid the skills; it is wrong when the control is `bare`, whose
    catalog is masked in the container (ADR-0049) — the control would be filed
    as "had 4 skills, opened none" and the design would be marked invalid on the
    strength of a treatment that never existed.
    """
    from harness_lab_app.models import Case, CaseType, HarnessSource
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
            experiment_id="bare-vs-harness",
            name="Bare vs full harness",
            question="Does the harness help at all?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["tetris-v1"],
            repetitions=1,
            conditions=[
                ExperimentConditionCreate(
                    label="bare",
                    role=ConditionRole.control,
                    # Deliberately not declaring the catalog it cannot see:
                    # correctness must not depend on the operator having
                    # written the declaration out. Its sibling disables those
                    # skills, and the sibling-removes rule would otherwise hand
                    # that list straight back to this arm.
                    overrides={"harness_source": HarnessSource.bare.value},
                ),
                ExperimentConditionCreate(
                    label="ablated",
                    role=ConditionRole.treatment,
                    overrides={"skills_disabled": governed},
                ),
            ],
        )
    )
    runs = service.launch("bare-vs-harness", _base_config())
    for run in runs:
        _score(session, run, passed=True, skill_invoked=0.0)

    summary = service.summarize("bare-vs-harness")

    arms = [
        issue["arm"]
        for issue in summary["validity"]["issues"]
        if issue["code"] in ("treatment_not_activated", "mixed_treatment")
    ]
    assert arms == []


def test_the_scorer_puts_no_skill_on_a_bare_arms_menu(session):
    """The other half of the same rule, one layer down (ADR-0049).

    `build_context` unions the siblings' skill fields into "what this run is
    about" and then intersects with what the run could see. For a bare arm what
    it could see is nothing at all, so the intersection must be empty — else
    `SkillUsageEvaluator` writes `skill_invoked = 0` and the control enters the
    corpus as an arm that ignored skills it was never given.
    """
    from harness_lab_app.models import Case, CaseType, HarnessSource
    from harness_lab_app.score_service import ScoreService
    from harness_lab_app.skills_catalog import known_skill_names

    governed = sorted(known_skill_names())[:3]
    if len(governed) < 3:
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
            experiment_id="bare-menu",
            name="Bare menu",
            question="Does the harness help at all?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["tetris-v1"],
            repetitions=1,
            conditions=[
                ExperimentConditionCreate(
                    label="bare",
                    role=ConditionRole.control,
                    overrides={"harness_source": HarnessSource.bare.value},
                ),
                ExperimentConditionCreate(
                    label="governed",
                    role=ConditionRole.treatment,
                    overrides={"skills_invoke": governed},
                ),
            ],
        )
    )
    runs = service.launch("bare-menu", _base_config())
    contexts = {
        run.condition_label: ScoreService(session).build_context(run.run_id)
        for run in runs
    }

    assert contexts["bare"].skills_under_test == ()
    assert sorted(contexts["governed"].skills_under_test) == governed


def test_the_case_matrix_reads_every_repetition_not_the_last(session):
    """A 1-of-3 pass and a 3-of-3 pass used to render identically.

    The cell kept whichever repetition came last, so "fixed" could mean the
    treatment passed once. A case counts as passed for an arm only when every
    scored repetition passed.
    """
    from harness_lab_app.models import Case, CaseType

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
            experiment_id="matrix-reps",
            name="Matrix over repetitions",
            question="Does the skill help?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["tetris-v1"],
            repetitions=3,
            conditions=[
                ExperimentConditionCreate(
                    label="control",
                    role=ConditionRole.control,
                    overrides={"skills_disabled": ["bootstrap_project"]},
                ),
                ExperimentConditionCreate(
                    label="treatment",
                    role=ConditionRole.treatment,
                    overrides={"skills_disabled": []},
                ),
            ],
        )
    )
    runs = service.launch("matrix-reps", _base_config())
    treatment_runs = [run for run in runs if run.condition_label == "treatment"]
    for run in runs:
        if run.condition_label == "control":
            _score(session, run, passed=False)
    # Treatment: fails twice, passes once — and the last one is the pass.
    for run, passed in zip(treatment_runs, (False, False, True), strict=True):
        _score(session, run, passed=passed)

    summary = service.summarize("matrix-reps")

    [row] = summary["comparisons"][0]["case_matrix"]
    assert row["treatment"] is False
    assert row["movement"] == "unchanged"
    assert summary["verdict"]["movements"]["fixed"] == 0


def _reps_design(service, experiment_id, repetitions=3):
    """One case, a control and a treatment: the smallest design with a delta."""
    from sqlmodel import select

    from harness_lab_app.models import Case, CaseType

    session = service.session
    if not session.exec(select(Case).where(Case.case_id == "tetris-v1")).first():
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
            experiment_id=experiment_id,
            name="Uncertainty",
            question="Does the skill help?",
            mode=ExperimentMode.harness_evaluation,
            case_ids=["tetris-v1"],
            repetitions=repetitions,
            conditions=[
                ExperimentConditionCreate(
                    label="control",
                    role=ConditionRole.control,
                    overrides={"skills_disabled": ["bootstrap_project"]},
                ),
                ExperimentConditionCreate(
                    label="treatment",
                    role=ConditionRole.treatment,
                    overrides={"skills_disabled": []},
                ),
            ],
        )
    )


def test_every_arm_reports_the_interval_around_its_success_rate(session):
    """A pass rate over three runs is a range, and the summary says which range.

    ADR-0064: the interval existed in `statistics.py` for months and reached
    neither the API nor the page, so "validation" was printed over numbers
    whose 95% interval spanned most of the scale.
    """
    from harness_lab_app.statistics import wilson_interval

    service = ExperimentService(session)
    _reps_design(service, "uncertainty")
    runs = service.launch("uncertainty", _base_config())
    treatment_runs = [run for run in runs if run.condition_label == "treatment"]
    for run in runs:
        if run.condition_label == "control":
            _score(session, run, passed=False)
    for run, passed in zip(treatment_runs, (True, True, False), strict=True):
        _score(session, run, passed=passed)

    summary = service.summarize("uncertainty")

    arms = {condition["label"]: condition for condition in summary["conditions"]}
    low, high = wilson_interval(2, 3)
    assert arms["treatment"]["success_rate_ci_low"] == pytest.approx(low)
    assert arms["treatment"]["success_rate_ci_high"] == pytest.approx(high)
    # 0 of 3 is not "certainly zero": the upper bound stays above half.
    assert arms["control"]["success_rate_ci_low"] == 0.0
    assert arms["control"]["success_rate_ci_high"] > 0.5
    # pass^k asks whether every try passes; two of three is not that.
    assert arms["treatment"]["pass_k"]["k"] == 3
    assert arms["treatment"]["pass_k"]["pass_at_k"] == pytest.approx(1.0)
    assert arms["treatment"]["pass_k"]["pass_hat_k"] == pytest.approx(0.0)


def test_the_success_delta_arrives_with_a_case_clustered_interval(session):
    service = ExperimentService(session)
    _reps_design(service, "delta-interval")
    runs = service.launch("delta-interval", _base_config())
    for run in runs:
        _score(session, run, passed=run.condition_label == "treatment")

    summary = service.summarize("delta-interval")

    success = summary["comparisons"][0]["success"]
    assert success["points"] == pytest.approx(100.0)
    assert success["paired_cases"] == 1
    assert success["delta_ci_low"] < success["points"]
    assert success["delta_ci_high"] == pytest.approx(100.0)
    assert success["interval_crosses_zero"] is (success["delta_ci_low"] <= 0.0)
    # Computed twice, rendered twice, the same both times.
    assert service.summarize("delta-interval")["comparisons"][0]["success"] == success


def test_the_summary_says_how_many_repetitions_a_20_point_difference_needs(session):
    """Advice for the next design, offered before anything has even run."""
    service = ExperimentService(session)
    _reps_design(service, "planning", repetitions=2)

    summary = service.summarize("planning")

    plan = summary["repetition_plan"]
    assert plan["basis"] == "assumed"
    assert plan["difference_points"] == 20
    assert plan["repetitions_per_case"] == plan["runs_per_arm"] == 93
    assert summary["repetitions_needed_for_20pp"] == 93
    # Nothing scored yet: no interval, no pass^k — and no invented ones.
    for condition in summary["conditions"]:
        assert condition["success_rate_ci_low"] is None
        assert condition["success_rate_ci_high"] is None
        assert condition["pass_k"] is None


def test_a_mode_with_no_comparison_gets_no_repetition_plan(lab):
    lab.create_experiment(
        _payload(
            experiment_id="nothing-to-compare",
            mode=ExperimentMode.discovery,
            repetitions=1,
            conditions=[
                ExperimentConditionCreate(
                    label="as-is",
                    role=ConditionRole.control,
                    overrides={"target_repo_id": "payments"},
                ),
            ],
        )
    )

    summary = lab.summarize("nothing-to-compare")

    assert summary["repetition_plan"] is None
    assert summary["repetitions_needed_for_20pp"] is None


def test_a_ladder_rung_is_not_objected_to_for_being_single_factor():
    """A rung of a family is single-factor by design (ADR-0051)."""
    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            _condition("governance-only", ConditionRole.control, skills_disabled=["a"]),
            _condition("skills-available", skills_disabled=[]),
        ],
        in_family=True,
    )

    assert not any("lab's own root" in warning for warning in warnings)


def test_an_activation_experiment_is_not_told_its_directories_differ():
    """`skills_invoke` moves no directory; the venue warning was false for it."""
    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            _condition("available", ConditionRole.control, skills_invoke=[]),
            _condition("activated", skills_invoke=["a", "b"]),
        ],
    )

    assert not any("skill directories differ" in warning for warning in warnings)


def test_a_family_id_travels_with_the_experiment(session):
    service = ExperimentService(session)
    experiment, _ = service.create_experiment(_payload(family_id="ladder-x"))

    assert experiment.family_id == "ladder-x"
    assert service.summarize(experiment.experiment_id)["family_id"] == "ladder-x"


# --------------------------------------------------------------------------
# Closing — an experiment stops being "running" when its runs stop
# --------------------------------------------------------------------------


def _finish(session, run_id: str, state: RunState) -> None:
    run = RunService(session).get_run(run_id)
    run.state = state
    session.add(run)
    session.commit()


def test_an_experiment_completes_once_every_run_has_stopped(lab, session):
    """`ExperimentStatus.complete` was in the enum and assigned by nothing.

    Both talos experiments were still `running` long after their last run had
    finished, which made "running" mean nothing in the attention feed.
    """
    lab.create_experiment(_payload(repetitions=1))
    runs = lab.launch(
        "migration-safety",
        RunConfigCreate(
            case_id="add-endpoint",
            prompt_variant="default",
            executor_model="stub-model",
        ),
    )

    _finish(session, runs[0].run_id, RunState.completed)
    assert lab.try_complete("migration-safety") is None, "one run is still going"
    assert lab.get_experiment("migration-safety").status == ExperimentStatus.running

    _finish(session, runs[1].run_id, RunState.completed)
    completed = lab.try_complete("migration-safety")

    assert completed is not None
    assert completed.status == ExperimentStatus.complete
    # Idempotent: only the call that closed it reports having closed it, so a
    # caller queues the reading exactly once.
    assert lab.try_complete("migration-safety") is None


def test_a_failed_run_still_closes_its_experiment(lab, session):
    """Closing is about runs having stopped, not about them having succeeded."""
    lab.create_experiment(_payload(repetitions=1))
    runs = lab.launch(
        "migration-safety",
        RunConfigCreate(
            case_id="add-endpoint",
            prompt_variant="default",
            executor_model="stub-model",
        ),
    )
    _finish(session, runs[0].run_id, RunState.failed)
    _finish(session, runs[1].run_id, RunState.awaiting_manual_score)

    assert lab.try_complete("migration-safety") is not None
    assert lab.get_experiment("migration-safety").status == ExperimentStatus.complete


def test_an_abandoned_experiment_is_never_reopened_as_complete(lab, session):
    lab.create_experiment(_payload(repetitions=1))
    runs = lab.launch(
        "migration-safety",
        RunConfigCreate(
            case_id="add-endpoint",
            prompt_variant="default",
            executor_model="stub-model",
        ),
    )
    for run in runs:
        _finish(session, run.run_id, RunState.completed)
    lab.abandon_experiment("migration-safety")

    assert lab.try_complete("migration-safety") is None
    assert lab.get_experiment("migration-safety").status == ExperimentStatus.abandoned


def _failed_check(command="make test", exit_code=2):
    return {
        "strategy": "tests",
        "passed": False,
        "checks": [
            {
                "command": command,
                "name": command,
                "exit_code": exit_code,
                "passed": False,
                "conclusive": True,
            }
        ],
        "summary": "0/1 checks passed",
    }


def _unmeasured_check(command="make test", reason="not_found"):
    return {
        "strategy": "tests",
        "passed": None,
        "checks": [
            {
                "command": command,
                "name": command,
                "exit_code": 127,
                "passed": False,
                "conclusive": False,
                "inconclusive_reason": reason,
            }
        ],
        "summary": "0/1 checks passed; 1 inconclusive (command not found)",
    }


def test_two_arms_failing_the_same_check_identically_is_a_floor(session):
    """Nothing resolved on either side, so the 0-point delta ranks nothing.

    This is `audit-ml-python-base`: every run failed the lab's own `make test`
    and the summary led with "No difference observed" (ADR-0062).
    """
    service = ExperimentService(session)
    # Two repetitions: below that the floor stays quiet for the same reason the
    # ceiling does — one failing run is evidence of one run.
    _staged_skill_design(service, "floored")
    runs = service.launch("floored", _base_config())
    for run in runs:
        _score(session, run, passed=False, objective=_failed_check())

    summary = service.summarize("floored")

    assert "floor_effect" in [i["code"] for i in summary["validity"]["issues"]]
    assert summary["verdict"]["headline"] == "Comparison not valid"


def test_an_experiment_whose_checks_never_ran_is_reported_unmeasured(session):
    """The runs completed; it is the checks that measured nothing."""
    service = ExperimentService(session)
    _skills_design(service, "unmeasured")
    runs = service.launch("unmeasured", _base_config())
    for run in runs:
        _score(session, run, passed=None, objective=_unmeasured_check())

    summary = service.summarize("unmeasured")

    codes = [issue["code"] for issue in summary["validity"]["issues"]]
    assert codes[0] == "objective_unmeasured"
    assert summary["validity"]["status"] == "invalid"
    assert "command not found" in summary["validity"]["issues"][0]["detail"]
    # Every arm reports the count behind the call, not just the verdict.
    assert all(
        health["unmeasured"] == health["runs"]
        for health in summary["run_health"].values()
    )


def test_a_manual_only_case_is_not_an_unmeasured_experiment(session):
    """It declares no checks: nothing was meant to be measured."""
    service = ExperimentService(session)
    _skills_design(service, "manualonly")
    runs = service.launch("manualonly", _base_config())
    for run in runs:
        _score(session, run, passed=None, objective={"passed": None, "checks": []})

    summary = service.summarize("manualonly")

    assert "objective_unmeasured" not in [
        issue["code"] for issue in summary["validity"]["issues"]
    ]


# --------------------------------------------------------------------------
# Harness x model — the interaction is the question (ADR-0066)
# --------------------------------------------------------------------------


def _grid(**over):
    """A 2x2: bare vs the lab harness, on two models."""
    conditions = []
    for model in ("small", "large"):
        for source in (HarnessSource.bare, HarnessSource.lab_root):
            conditions.append(
                ExperimentConditionCreate(
                    label=f"{source.value}-{model}",
                    role=ConditionRole.control
                    if (source == HarnessSource.bare and model == "small")
                    else ConditionRole.treatment,
                    overrides={"harness_source": source, "executor_model": model},
                )
            )
    base = {
        "experiment_id": "less-harness",
        "question": "Do stronger models need less harness?",
        "mode": ExperimentMode.harness_by_model,
        "case_ids": ["add-endpoint"],
        "prompt_variants": ["default"],
        "repetitions": 3,
        "conditions": conditions,
    }
    base.update(over)
    return ExperimentCreate(**base)


def test_harness_by_model_accepts_a_full_grid_where_harness_evaluation_refuses_it(lab):
    experiment, _ = lab.create_experiment(_grid())
    assert experiment.mode == ExperimentMode.harness_by_model
    with pytest.raises(ExperimentDesignError, match="confounded"):
        lab.create_experiment(
            _grid(experiment_id="refused", mode=ExperimentMode.harness_evaluation)
        )


def test_harness_by_model_refuses_an_incomplete_grid(lab):
    payload = _grid()
    payload.conditions = payload.conditions[:3]
    with pytest.raises(ExperimentDesignError, match="incomplete"):
        lab.create_experiment(payload)


def test_harness_by_model_refuses_a_second_varying_harness_factor(lab):
    payload = _grid()
    for condition in payload.conditions:
        if condition.overrides["harness_source"] == HarnessSource.lab_root:
            condition.overrides["skills_invoke"] = ["verify_changes"]
    with pytest.raises(ExperimentDesignError, match="one harness"):
        lab.create_experiment(payload)


def test_harness_by_model_keeps_the_declared_model_order(lab):
    """Labels sort alphabetically on read ('bare-large' before 'bare-small'), but
    the interaction runs from the first model declared to the last — reading it
    the other way round flips its sign."""
    lab.create_experiment(_grid())
    block = lab.summarize("less-harness")["factorial"]
    assert block["models"] == ["small", "large"]
    assert block["interaction"]["from_model"] == "small"
    assert block["interaction"]["to_model"] == "large"


def test_harness_by_model_order_does_not_depend_on_which_arm_is_the_control(lab):
    """Declared small→large, control on the *large* model: the reading must still
    run small→large. The control role sorts first on read and used to drag its
    model to the front."""
    payload = _grid()
    for condition in payload.conditions:
        condition.role = (
            ConditionRole.control
            if condition.label == "bare-large"
            else ConditionRole.treatment
        )
    lab.create_experiment(payload)
    block = lab.summarize("less-harness")["factorial"]
    assert block["models"] == ["small", "large"]
    assert block["harness_levels"] == ["no harness", "lab harness"]


def _cost(session, run, usd):
    from harness_lab_app.models import Score

    session.add(Score(run_id=run.run_id, name="cost_usd", version=1, value_num=usd))
    session.commit()


def test_harness_by_model_cost_per_resolved_counts_only_scored_runs_with_cost(lab):
    """Cost per resolved task divides the cost of the runs it counts by their
    passes. A run with no cost figure is not $0: the cell reports no figure."""
    lab.create_experiment(_grid(repetitions=2))
    runs = lab.launch(
        "less-harness",
        RunConfigCreate(case_id="add-endpoint", prompt_variant="default"),
    )
    for run in runs:
        _score(lab.session, run, passed=True)
        if run.condition_label != "lab_root-large":
            _cost(lab.session, run, 1.0)
    # lab_root-large: one run has a cost, the other does not.
    _cost(
        lab.session, next(r for r in runs if r.condition_label == "lab_root-large"), 1.0
    )

    cells = {
        (c["level"], c["model"]): c
        for c in lab.summarize("less-harness")["factorial"]["cells"]
    }
    assert cells[("no harness", "small")]["cost_per_resolved_usd"] == pytest.approx(1.0)
    assert cells[("lab harness", "large")]["cost_usd"] is None
    assert cells[("lab harness", "large")]["cost_per_resolved_usd"] is None


def test_harness_by_model_summary_reads_the_interaction(lab):
    lab.create_experiment(_grid())
    runs = lab.launch(
        "less-harness",
        RunConfigCreate(case_id="add-endpoint", prompt_variant="default"),
    )
    assert len(runs) == 12  # 4 cells x 3 repetitions
    # small: bare 0/3 → harness 3/3 ; large: bare 3/3 → harness 3/3
    passes = {
        "bare-small": False,
        "lab_root-small": True,
        "bare-large": True,
        "lab_root-large": True,
    }
    for run in runs:
        _score(lab.session, run, passed=passes[run.condition_label])

    block = lab.summarize("less-harness")["factorial"]

    assert block["axis"] == "harness_source"
    assert block["models"] == ["small", "large"]
    assert block["harness_levels"] == ["no harness", "lab harness"]
    effects = {row["model"]: row["points"] for row in block["harness_effect"]}
    assert effects == {"small": pytest.approx(100.0), "large": pytest.approx(0.0)}
    assert block["interaction"]["points"] == pytest.approx(-100.0)
    # One case cannot establish anything, however large the observed change.
    assert block["interaction"]["established"] is False


def test_harness_by_model_verdict_points_to_the_panel_not_to_missing_evidence(lab):
    lab.create_experiment(_grid())
    runs = lab.launch(
        "less-harness",
        RunConfigCreate(case_id="add-endpoint", prompt_variant="default"),
    )
    for run in runs:
        _score(lab.session, run, passed=True)
    verdict = lab.summarize("less-harness")["verdict"]
    assert verdict["headline"] != "Not enough evidence yet"
    assert "harness x model" in verdict["headline"].lower()
    assert verdict["causal"] is False


def test_tier_tags_give_each_arm_a_capability_frontier(lab):
    """Cases tagged tier:1 / tier:2 turn any experiment into a capability ladder
    per arm (ADR-0069): the highest tier the arm does reliably."""
    for case_id, tier in [
        ("t1a", "1"),
        ("t1b", "1"),
        ("t1c", "1"),
        ("t1d", "1"),
        ("t2a", "2"),
        ("t2b", "2"),
        ("t2c", "2"),
        ("t2d", "2"),
    ]:
        CaseService(lab.session).create_case(
            CaseCreate(
                case_id=case_id,
                name=case_id,
                type=CaseType.repo_task,
                prompt_variants={"default": "do it"},
                tags=[f"tier:{tier}"],
            )
        )
    lab.create_experiment(
        _grid(
            experiment_id="ladder",
            case_ids=["t1a", "t1b", "t1c", "t1d", "t2a", "t2b", "t2c", "t2d"],
            repetitions=5,
        )
    )
    runs = lab.launch(
        "ladder", RunConfigCreate(case_id="t1a", prompt_variant="default")
    )
    run_service = RunService(lab.session)
    for run in runs:
        case_id = run_service.get_run_config(run.run_config_id).case_id
        # Every arm solves tier 1; only the large model with the harness solves tier 2.
        passed = case_id.startswith("t1") or run.condition_label == "lab_root-large"
        _score(lab.session, run, passed=passed)

    block = lab.summarize("ladder")["capability"]
    frontier = {arm["label"]: arm["frontier"] for arm in block["arms"]}
    assert block["tiers"] == ["1", "2"]
    assert frontier["lab_root-large"] == "2"
    assert frontier["bare-small"] == "1"


def test_untagged_cases_produce_no_capability_block(lab):
    lab.create_experiment(_grid(experiment_id="plain"))
    assert lab.summarize("plain")["capability"] is None


def test_a_partial_vs_full_harness_ladder_runs_as_harness_by_model(lab):
    """none → governance only → full, on two models: the effect of each rung is
    read per model against 'none'."""
    conditions = []
    for model in ("small", "large"):
        for label, overrides in [
            ("none", {"harness_source": HarnessSource.bare}),
            (
                "gov",
                {
                    "harness_source": HarnessSource.lab_root,
                    "skills_disabled": ["verify_changes"],
                },
            ),
            ("full", {"harness_source": HarnessSource.lab_root}),
        ]:
            conditions.append(
                ExperimentConditionCreate(
                    label=f"{label}-{model}",
                    role=ConditionRole.control
                    if (label, model) == ("none", "small")
                    else ConditionRole.treatment,
                    overrides={**overrides, "executor_model": model},
                )
            )
    lab.create_experiment(_grid(experiment_id="ladder3", conditions=conditions))
    block = lab.summarize("ladder3")["factorial"]
    assert block["axis"] == "harness_level"
    assert len(block["harness_levels"]) == 3
    rungs = {(row["model"], row["level"]) for row in block["harness_effect"]}
    assert len(rungs) == 4  # two rungs above 'none', on two models
