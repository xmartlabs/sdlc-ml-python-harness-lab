"""Per-step reward: progress per step, credit per step, and the run's summary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_lab_app import step_reward


def _objective(passed: int, total: int, inconclusive: int = 0) -> dict:
    checks = [{"passed": True, "conclusive": True} for _ in range(passed)]
    checks += [
        {"passed": False, "conclusive": True}
        for _ in range(total - passed - inconclusive)
    ]
    checks += [
        {"passed": False, "conclusive": False, "inconclusive_reason": "timeout"}
        for _ in range(inconclusive)
    ]
    return {"checks": checks}


# --------------------------------------------------------------------------
# progress_of
# --------------------------------------------------------------------------


def test_progress_is_the_share_of_conclusive_checks_that_pass() -> None:
    assert step_reward.progress_of(_objective(3, 4)) == pytest.approx(0.75)


def test_progress_excludes_inconclusive_checks_from_the_denominator() -> None:
    # A timed-out check measured the sandbox, not the agent (ADR-0048).
    assert step_reward.progress_of(_objective(2, 4, inconclusive=2)) == 1.0


def test_progress_is_none_when_nothing_was_measured() -> None:
    assert step_reward.progress_of({"checks": []}) is None
    assert step_reward.progress_of(_objective(0, 2, inconclusive=2)) is None
    assert step_reward.progress_of(None) is None


# --------------------------------------------------------------------------
# build_trajectory — the K-019 shape
# --------------------------------------------------------------------------


def _steps(*rows: tuple[float | None, float | None]) -> list[dict]:
    return [
        {"step": index, "progress": progress, "cost_usd": cost}
        for index, (progress, cost) in enumerate(rows, start=1)
    ]


def test_a_run_solved_in_step_one_spends_the_rest_after_resolution() -> None:
    # tetris-v2-K-019 as replayed: 10/10 after step 1, four steps, $1.08.
    trajectory = step_reward.build_trajectory(
        _steps((1.0, 0.29), (1.0, 0.27), (1.0, 0.26), (1.0, 0.26))
    )
    assert trajectory.steps_to_resolve == 1
    assert trajectory.resolved
    assert trajectory.cost_to_resolve_usd == pytest.approx(0.29)
    assert trajectory.post_resolution_cost_share == pytest.approx(0.79 / 1.08)
    assert trajectory.progress_auc == 1.0
    assert trajectory.step_regressions == 0
    assert [step.reward for step in trajectory.steps] == [1.0, 0.0, 0.0, 0.0]


def test_the_rewards_telescope_to_the_final_progress() -> None:
    trajectory = step_reward.build_trajectory(
        _steps((0.2, 0.1), (0.6, 0.1), (0.4, 0.1), (0.9, 0.1))
    )
    assert sum(step.reward for step in trajectory.steps) == pytest.approx(0.9)


def test_a_step_that_breaks_passing_checks_is_a_regression() -> None:
    trajectory = step_reward.build_trajectory(
        _steps((0.5, 0.1), (0.8, 0.1), (0.3, 0.1), (0.8, 0.1))
    )
    assert trajectory.step_regressions == 1
    assert trajectory.steps[2].reward == pytest.approx(-0.5)
    assert not trajectory.resolved


def test_resolution_is_the_first_step_at_the_runs_best_progress() -> None:
    # Not resolved to 1.0, but the best it reached was at step 2: steps 3-4
    # bought nothing, and that is what cost_to_best measures.
    trajectory = step_reward.build_trajectory(
        _steps((0.4, 0.1), (0.7, 0.2), (0.7, 0.3), (0.7, 0.4))
    )
    assert trajectory.steps_to_resolve is None
    assert trajectory.cost_to_resolve_usd is None
    assert trajectory.steps_to_best == 2
    assert trajectory.post_best_cost_share == pytest.approx(0.7)
    assert trajectory.post_resolution_cost_share is None


def test_progress_auc_rewards_arriving_early() -> None:
    early = step_reward.build_trajectory(_steps((1.0, 0.1), (1.0, 0.1)))
    late = step_reward.build_trajectory(_steps((0.0, 0.1), (1.0, 0.1)))
    assert early.progress_auc > late.progress_auc
    assert late.progress_auc == pytest.approx(0.5)


def test_an_unmeasured_step_keeps_the_last_known_progress() -> None:
    trajectory = step_reward.build_trajectory(
        _steps((0.5, 0.1), (None, 0.1), (1.0, 0.1))
    )
    assert trajectory.steps[1].reward is None
    assert trajectory.steps[2].reward == pytest.approx(0.5)
    assert trajectory.unmeasured_steps == 1
    assert trajectory.steps_to_resolve == 3


def test_cost_shares_are_none_when_any_step_cost_is_unknown() -> None:
    trajectory = step_reward.build_trajectory(_steps((1.0, 0.1), (1.0, None)))
    assert trajectory.steps_to_resolve == 1
    assert trajectory.cost_to_resolve_usd == pytest.approx(0.1)
    assert trajectory.post_resolution_cost_share is None


def test_a_free_run_has_no_cost_share_but_still_has_token_shares() -> None:
    steps = [
        {"step": 1, "progress": 1.0, "cost_usd": 0.0, "billed_tokens": 100},
        {"step": 2, "progress": 1.0, "cost_usd": 0.0, "billed_tokens": 300},
    ]
    trajectory = step_reward.build_trajectory(steps)
    assert trajectory.post_resolution_cost_share is None
    assert trajectory.post_resolution_token_share == pytest.approx(0.75)


def test_no_measured_step_gives_an_empty_summary() -> None:
    trajectory = step_reward.build_trajectory(_steps((None, 0.1), (None, 0.1)))
    assert trajectory.steps_to_resolve is None
    assert trajectory.progress_auc is None
    assert not trajectory.measured


# --------------------------------------------------------------------------
# credit: what was in context when progress happened
# --------------------------------------------------------------------------


def test_credit_attributes_each_steps_reward_to_what_it_consulted() -> None:
    steps = _steps((0.2, 0.1), (1.0, 0.1), (1.0, 0.1))
    steps[0]["consulted"] = ["doc:.github/standards.md"]
    steps[1]["consulted"] = ["skill:systematic_debugging", "doc:.github/standards.md"]
    steps[2]["consulted"] = ["skill:verify_changes"]
    trajectory = step_reward.build_trajectory(steps)
    credit = {row["item"]: row for row in trajectory.credit}
    assert credit["skill:systematic_debugging"]["reward_when_consulted"] == (
        pytest.approx(0.8)
    )
    assert credit["doc:.github/standards.md"]["steps"] == [1, 2]
    assert credit["skill:verify_changes"]["reward_when_consulted"] == 0.0
    # Never stated as causal: the field says what it is.
    assert all(row["kind"] == "co_occurrence" for row in trajectory.credit)


# --------------------------------------------------------------------------
# on-disk step records written by the runner
# --------------------------------------------------------------------------


def test_snapshot_dirs_are_listed_in_step_order(tmp_path: Path) -> None:
    steps = tmp_path / "steps"
    for n in (10, 2, 1):
        (steps / f"step-{n}.snapshot").mkdir(parents=True)
    (steps / "step-3.log").write_text("not a snapshot")
    found = step_reward.snapshot_dirs(tmp_path)
    assert [n for n, _ in found] == [1, 2, 10]


def test_step_meta_is_read_per_step_and_tolerates_garbage(tmp_path: Path) -> None:
    steps = tmp_path / "steps"
    steps.mkdir()
    (steps / "step-1.meta.json").write_text(
        json.dumps({"step": 1, "cost_usd": 0.2, "billed_tokens": 5})
    )
    (steps / "step-2.meta.json").write_text("{broken")
    meta = step_reward.read_step_meta(tmp_path)
    assert meta[1]["cost_usd"] == 0.2
    assert 2 not in meta


def test_claude_step_cost_comes_from_the_result_event() -> None:
    lines = [
        json.dumps({"type": "assistant", "message": {"content": []}}),
        json.dumps(
            {
                "type": "result",
                "total_cost_usd": 0.31,
                "num_turns": 4,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 5,
                },
            }
        ),
    ]
    cost = step_reward.claude_step_cost(lines)
    assert cost == {
        "cost_usd": 0.31,
        "billed_tokens": 135,
        "output_tokens": 20,
        "turns": 4,
    }


def test_claude_step_cost_is_empty_without_a_result_event() -> None:
    assert step_reward.claude_step_cost(["not json", json.dumps({"type": "x"})]) == {}


def test_consulted_items_name_skills_and_governance_docs() -> None:
    parsed = {
        "skills_consulted": ["verify_changes"],
        "files_read": [".github/standards.md", "main.py", "memory/context.md"],
    }
    assert step_reward.consulted_items(
        parsed, governance=(".github/standards.md", "memory/context.md")
    ) == [
        "doc:.github/standards.md",
        "doc:memory/context.md",
        "skill:verify_changes",
    ]


def test_corpus_credit_lists_never_opened_items_first() -> None:
    run_a = step_reward.build_trajectory(
        [
            {"step": 1, "progress": 1.0, "consulted": ["skill:verify_changes"]},
        ]
    ).as_dict()
    run_b = step_reward.build_trajectory(
        [
            {"step": 1, "progress": 0.5, "consulted": ["skill:verify_changes"]},
            {"step": 2, "progress": 1.0, "consulted": ["skill:systematic_debugging"]},
        ]
    ).as_dict()
    unmeasured = step_reward.build_trajectory([{"step": 1, "progress": None}]).as_dict()

    rows = step_reward.corpus_credit(
        [run_a, run_b, unmeasured],
        available=["skill:verify_changes", "skill:brainstorm_quick"],
    )

    assert [row["item"] for row in rows] == [
        "skill:brainstorm_quick",
        "skill:systematic_debugging",
        "skill:verify_changes",
    ]
    by_item = {row["item"]: row for row in rows}
    assert by_item["skill:brainstorm_quick"]["runs"] == 0
    assert by_item["skill:verify_changes"]["runs"] == 2
    assert by_item["skill:verify_changes"]["share_of_runs"] == 1.0
    assert by_item["skill:verify_changes"]["reward_when_consulted"] == 1.5


# --------------------------------------------------------------------------
# StepRewardService: reading curves out of the store
# --------------------------------------------------------------------------


def test_the_service_reads_one_run_and_ranks_the_corpus(monkeypatch) -> None:
    from sqlmodel import Session, SQLModel, create_engine

    from harness_lab_app import skills_catalog
    from harness_lab_app.models import Validation
    from harness_lab_app.step_reward_service import StepRewardService

    monkeypatch.setattr(
        skills_catalog, "known_skill_names", lambda: {"verify_changes", "unused"}
    )
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    measured = step_reward.build_trajectory(
        [{"step": 1, "progress": 1.0, "consulted": ["skill:verify_changes"]}]
    ).as_dict()
    with Session(engine) as session:
        session.add(Validation(run_id="r1", objective={"per_step": measured}))
        session.add(Validation(run_id="r2", objective={"passed": True}))
        session.commit()
        service = StepRewardService(session)

        assert service.trajectory("r1")["steps_to_resolve"] == 1
        assert service.trajectory("r2") is None
        assert service.trajectory("missing") is None

        corpus = service.corpus()
        assert corpus["runs_measured"] == 1
        assert [row["item"] for row in corpus["items"]] == [
            "skill:unused",
            "skill:verify_changes",
        ]
        assert service.corpus(run_ids=["r2"])["runs_measured"] == 0


def test_shell_reads_of_the_harness_count_as_consulted() -> None:
    surface = (".github/standards.md", "memory/context.md")
    items = step_reward.shell_read_items(
        [
            "wc -l .github/standards.md; cat .github/standards.md | head -80",
            "sed -n 1,40p .claude/skills/verify_changes/SKILL.md",
            "ls memory/context.md",  # listing is not reading
            "python main.py",
        ],
        surface,
    )
    assert items == {"doc:.github/standards.md", "skill:verify_changes"}


def test_per_step_consulted_reads_both_lanes(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from sqlmodel import Session, SQLModel, create_engine

    from harness_lab_app import attribution_service

    run_dir = tmp_path / "run-x"
    steps = run_dir / "steps"
    steps.mkdir(parents=True)
    # claude step: a Read tool on a governance doc, a shell read of a skill.
    (steps / "step-1.events.jsonl").write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "a",
                                "name": "Read",
                                "input": {"file_path": "/app/.github/standards.md"},
                            },
                            {
                                "type": "tool_use",
                                "id": "b",
                                "name": "Bash",
                                "input": {
                                    "command": "cat .claude/skills/verify_changes/SKILL.md"
                                },
                            },
                        ]
                    },
                }
            ]
        )
    )
    (steps / "step-1.log").write_text("ignored: the events file wins")
    # opencode step: a shell read of a governance doc.
    (steps / "step-2.log").write_text(
        'message=evaluated permission=bash pattern="cat .github/standards.md" '
        "action.action=allow\n"
    )
    monkeypatch.setattr(
        attribution_service,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path, repo_root=tmp_path),
    )
    monkeypatch.setattr(
        attribution_service.AttributionService,
        "_governance",
        lambda _self, _run_dir, _meta: {"reading_surface": (".github/standards.md",)},
    )
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        found = attribution_service.AttributionService(session).per_step_consulted(
            "run-x"
        )
    assert found == {
        1: ["doc:.github/standards.md", "skill:verify_changes"],
        2: ["doc:.github/standards.md"],
    }


def test_a_single_step_run_reads_its_one_step_from_the_run_root(tmp_path) -> None:
    # The snapshot creates steps/, but the step's own log and events stay at
    # the run root: the one-step layout every other reader falls back to.
    (tmp_path / "steps" / "step-1.snapshot").mkdir(parents=True)
    (tmp_path / "opencode.log").write_text("log")
    (tmp_path / "opencode.events.jsonl").write_text("{}")
    files = step_reward.step_record_files(tmp_path)
    assert set(files) == {1}
    assert files[1]["log"].name == "opencode.log"
    assert files[1]["events"].name == "opencode.events.jsonl"


def test_multi_step_records_win_over_the_combined_log(tmp_path) -> None:
    steps = tmp_path / "steps"
    steps.mkdir()
    (steps / "step-1.log").write_text("a")
    (steps / "step-2.log").write_text("b")
    (steps / "step-2.events.jsonl").write_text("{}")
    (steps / "step-2-prompt.md").write_text("not a record")
    (tmp_path / "opencode.log").write_text("combined")
    files = step_reward.step_record_files(tmp_path)
    assert set(files) == {1, 2}
    assert set(files[2]) == {"log", "events"}


def test_swe_progress_is_the_issue_not_the_tests_that_already_passed() -> None:
    untouched = {"f2p_passed": 0, "f2p_total": 1, "p2p_passed": 18, "p2p_total": 18}
    assert step_reward.swe_progress(untouched) == 0.0  # not 18/19
    half = {"f2p_passed": 1, "f2p_total": 2, "p2p_passed": 18, "p2p_total": 18}
    assert step_reward.swe_progress(half) == 0.5
    broke = {"f2p_passed": 2, "f2p_total": 2, "p2p_passed": 9, "p2p_total": 18}
    assert step_reward.swe_progress(broke) == 0.5  # fixed it, broke half the rest
    done = {"resolved": True, "f2p_passed": 1, "f2p_total": 1}
    assert step_reward.swe_progress(done) == 1.0
    assert step_reward.swe_progress({"f2p_total": 0}) is None


# --------------------------------------------------------------------------
# Re-scoring never leaves a stale step-reward score behind (PR #45 review)
# --------------------------------------------------------------------------


@pytest.fixture
def scored_session():
    """One in-memory database shared by every connection the service opens."""
    from sqlmodel import Session, SQLModel, create_engine
    from sqlmodel.pool import StaticPool

    # Registers every table on the metadata before it is created; run alone,
    # nothing else has imported the models yet.
    import harness_lab_app.models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session


def _seed_scored_run(session, tmp_path, monkeypatch, run_id: str = "run-r"):
    from types import SimpleNamespace

    from harness_lab_app import score_service as score_service_module
    from harness_lab_app.models import Case, CaseType, Run, RunConfig, Validation

    session.add(
        Case(
            case_id="c1",
            name="c1",
            type=CaseType.build_from_scratch,
            prompt_variants={"default": "Do it."},
        )
    )
    config = RunConfig(case_id="c1", prompt_variant="default")
    session.add(config)
    session.commit()
    session.refresh(config)
    session.add(Run(run_id=run_id, run_config_id=config.id))
    session.add(Validation(run_id=run_id))
    session.commit()
    monkeypatch.setattr(
        score_service_module,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path, repo_root=tmp_path),
    )


def _set_trajectory(session, run_id: str, rows: list[dict] | None) -> None:
    from sqlmodel import select

    from harness_lab_app.models import Validation

    validation = session.exec(
        select(Validation).where(Validation.run_id == run_id)
    ).one()
    objective = dict(validation.objective or {})
    if rows is None:
        objective.pop("per_step", None)
    else:
        objective["per_step"] = step_reward.build_trajectory(rows).as_dict()
    validation.objective = objective
    session.add(validation)
    session.commit()


def _step_scores(session, run_id: str) -> dict[str, float | None]:
    from sqlmodel import select

    from harness_lab_app.models import Score

    return {
        row.name: row.value_num
        for row in session.exec(select(Score).where(Score.run_id == run_id)).all()
        if row.source == "step_reward"
    }


def test_a_run_that_stops_resolving_loses_its_resolution_scores(
    scored_session, tmp_path, monkeypatch
) -> None:
    session = scored_session
    from harness_lab_app.score_service import ScoreService

    _seed_scored_run(session, tmp_path, monkeypatch)
    _set_trajectory(
        session,
        "run-r",
        [
            {"step": 1, "progress": 1.0, "cost_usd": 0.3},
            {"step": 2, "progress": 1.0, "cost_usd": 0.3},
        ],
    )
    ScoreService(session).evaluate_run("run-r", profile="no_judgement")
    assert _step_scores(session, "run-r")["steps_to_resolve"] == 1.0

    # Re-validated after a grader fix: it never actually resolved.
    _set_trajectory(
        session,
        "run-r",
        [
            {"step": 1, "progress": 0.5, "cost_usd": 0.3},
            {"step": 2, "progress": 0.5, "cost_usd": 0.3},
        ],
    )
    ScoreService(session).evaluate_run("run-r", profile="no_judgement")

    scores = _step_scores(session, "run-r")
    assert set(scores) == {"progress_auc", "step_regressions"}
    assert scores["progress_auc"] == 0.5


def test_a_run_whose_curve_disappears_loses_every_step_reward_score(
    scored_session, tmp_path, monkeypatch
) -> None:
    session = scored_session
    from harness_lab_app.score_service import ScoreService

    _seed_scored_run(session, tmp_path, monkeypatch)
    _set_trajectory(session, "run-r", [{"step": 1, "progress": 1.0, "cost_usd": 0.3}])
    ScoreService(session).evaluate_run("run-r", profile="no_judgement")
    assert _step_scores(session, "run-r")

    _set_trajectory(session, "run-r", None)
    ScoreService(session).evaluate_run("run-r", profile="no_judgement")

    assert _step_scores(session, "run-r") == {}


def test_a_profile_without_the_evaluator_leaves_its_scores_alone(
    scored_session, tmp_path, monkeypatch
) -> None:
    session = scored_session
    """Invalidation is the emitting evaluator's, never another profile's."""
    from harness_lab_app.score_service import ScoreService

    _seed_scored_run(session, tmp_path, monkeypatch)
    _set_trajectory(session, "run-r", [{"step": 1, "progress": 1.0, "cost_usd": 0.3}])
    ScoreService(session).evaluate_run("run-r", profile="no_judgement")
    before = _step_scores(session, "run-r")

    ScoreService(session).evaluate_run("run-r", profile="outcome_only")

    assert _step_scores(session, "run-r") == before
