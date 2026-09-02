"""SWE-bench auto-scoring: the objective verdict completes the run by itself."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine, select

from harness_lab_app import tasks as tasks_module
from harness_lab_app.case_service import CaseService
from harness_lab_app.models import (
    Case,
    CaseStatus,
    CaseType,
    RunState,
    Validation,
)
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate
from harness_lab_app.tasks import _autoscore_run
from harness_lab_app.validation_service import ValidationService

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_run  # noqa: E402


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _swe_spec() -> dict:
    return {
        "instance_id": "pallets__flask-4992",
        "repo": "pallets/flask",
        "base_commit": "4c288bc97ea371817199908d0d9b12de9dae327e",
        "problem_statement": "Add text mode param.",
        "test_patch": "diff --git a/x b/x\n",
        "fail_to_pass": ["tests/test_config.py::test_config_from_file_toml"],
        "pass_to_pass": ["tests/test_config.py::test_config_from_pyfile"],
        "version": "2.3",
        "repo_url": "https://github.com/pallets/flask.git",
        "python": "3.11",
        "install_pins": [],
    }


def _seed_swe_run(session: Session, run_id: str, state: RunState) -> None:
    CaseService(session).import_swebench_instance(_swe_spec())
    service = RunService(session)
    service.create_run(
        RunCreate(
            run_id=run_id,
            config=RunConfigCreate(
                case_id="pallets__flask-4992",
                prompt_variant="swe_default",
                executor_model="stub-executor",
            ),
        )
    )
    run = service.get_run(run_id)
    run.state = state
    session.add(run)
    session.commit()


def _store_validation(session: Session, run_id: str, passed: bool) -> None:
    summary = f"resolved={passed} F2P=1/1 P2P=18/18"
    session.add(
        Validation(
            run_id=run_id,
            objective={"strategy": "tests", "passed": passed, "summary": summary},
        )
    )
    session.commit()


def test_autoscore_resolves_run_to_10():
    with _session() as session:
        _seed_swe_run(session, "swe-auto-1", RunState.awaiting_manual_score)
        _store_validation(session, "swe-auto-1", passed=True)
        _autoscore_run(session, "swe-auto-1")
        run = RunService(session).get_run("swe-auto-1")
        assert run.state == RunState.completed
        assert run.score == 10
        assert "auto-scored" in run.notes


def test_autoscore_not_resolved_scores_0():
    with _session() as session:
        _seed_swe_run(session, "swe-auto-2", RunState.awaiting_manual_score)
        _store_validation(session, "swe-auto-2", passed=False)
        _autoscore_run(session, "swe-auto-2")
        run = RunService(session).get_run("swe-auto-2")
        assert run.state == RunState.completed
        assert run.score == 0


def test_autoscore_skips_without_validation_and_wrong_state():
    with _session() as session:
        _seed_swe_run(session, "swe-auto-3", RunState.awaiting_manual_score)
        # No validation → stays awaiting (manual fallback).
        _autoscore_run(session, "swe-auto-3")
        assert (
            RunService(session).get_run("swe-auto-3").state
            == RunState.awaiting_manual_score
        )

    with _session() as session:
        _seed_swe_run(session, "swe-auto-4", RunState.failed)
        _store_validation(session, "swe-auto-4", passed=True)
        # Terminal-but-not-awaiting runs are never rescored.
        _autoscore_run(session, "swe-auto-4")
        assert RunService(session).get_run("swe-auto-4").state == RunState.failed


def test_autoscore_skips_a_case_it_cannot_resolve():
    with _session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="tetris-auto-1",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                ),
            )
        )
        run = service.get_run("tetris-auto-1")
        run.state = RunState.awaiting_manual_score
        session.add(run)
        session.commit()
        # Unknown case (not in DB) → best-effort no-op, never a failed run.
        _autoscore_run(session, "tetris-auto-1")
        assert service.get_run("tetris-auto-1").state == RunState.awaiting_manual_score


_RUBRIC = (
    "| Level | Meaning |\n"
    "| 0 | No useful code |\n"
    "| 2 | Runs without crashing |\n"
    "| 10 | Playable |\n"
)


def _seed_rubric_run(session: Session, run_id: str, rubric: str) -> None:
    session.add(
        Case(
            case_id="tetris-v1",
            name="tetris-v1",
            type=CaseType.build_from_scratch,
            status=CaseStatus.active,
            scoring_rubric=rubric,
        )
    )
    session.commit()
    service = RunService(session)
    service.create_run(
        RunCreate(
            run_id=run_id,
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                executor_model="stub-executor",
            ),
        )
    )
    run = service.get_run(run_id)
    run.state = RunState.awaiting_manual_score
    session.add(run)
    session.commit()


def test_a_rubric_case_is_graded_against_its_own_rubric(tmp_path, monkeypatch):
    """The blank a human never fills in is worse than an opinion on the record."""
    runs = tmp_path / "runs"
    monkeypatch.setattr(
        tasks_module, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs)
    )
    with _session() as session:
        _seed_rubric_run(session, "tetris-auto-2", _RUBRIC)
        (runs / "tetris-auto-2").mkdir(parents=True)
        (runs / "tetris-auto-2" / "prompt.md").write_text("Build Tetris in pygame.")
        (runs / "tetris-auto-2" / "diff.patch").write_text("+def rotate(piece):")
        (runs / "tetris-auto-2" / "report.md").write_text("Implemented rotation.")

        def fake_gateway(model: str, system: str, user: str) -> str:
            # The rubric leads, and the report is framed as a claim to check.
            assert "Runs without crashing" in user
            assert user.index("## Rubric") < user.index("## Agent diff")
            assert "verify against the diff" in user
            return '{"score": 7, "rationale": "level 7: rotation present"}'

        _autoscore_run(session, "tetris-auto-2", gateway=fake_gateway)
        run = RunService(session).get_run("tetris-auto-2")
        assert run.state == RunState.completed
        assert run.score == 7
        validation = ValidationService(session).get_validation("tetris-auto-2")
        assert validation is not None
        # An opinion is recorded as an opinion, and a human can overwrite it.
        assert validation.subjective["score_source"] == "llm_judge"


def test_a_rubric_stored_as_a_repo_path_is_read_from_disk(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "scoring.md").write_text("| 5 | Piece falls |")
    monkeypatch.setattr(
        tasks_module,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=runs, repo_root=tmp_path),
    )
    with _session() as session:
        _seed_rubric_run(session, "tetris-auto-3", "benchmarks/scoring.md")
        (runs / "tetris-auto-3").mkdir(parents=True)
        (runs / "tetris-auto-3" / "diff.patch").write_text("+piece.y += 1")

        def fake_gateway(model: str, system: str, user: str) -> str:
            assert "Piece falls" in user
            return '{"score": 5, "rationale": "level 5"}'

        _autoscore_run(session, "tetris-auto-3", gateway=fake_gateway)
        assert RunService(session).get_run("tetris-auto-3").score == 5


def test_a_case_without_a_rubric_still_goes_to_a_human(tmp_path, monkeypatch):
    """No scale means no score: the judge is never asked to invent one."""
    runs = tmp_path / "runs"
    monkeypatch.setattr(
        tasks_module, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs)
    )
    with _session() as session:
        _seed_rubric_run(session, "tetris-auto-4", "")
        (runs / "tetris-auto-4").mkdir(parents=True)
        (runs / "tetris-auto-4" / "diff.patch").write_text("+something")

        def never(model: str, system: str, user: str) -> str:
            raise AssertionError("the judge must not be called without a rubric")

        _autoscore_run(session, "tetris-auto-4", gateway=never)
        assert (
            RunService(session).get_run("tetris-auto-4").state
            == RunState.awaiting_manual_score
        )


def _seed_swe_pro_run(session: Session, run_id: str, state: RunState) -> None:
    session.add(
        Case(
            case_id="pro-case",
            name="pro-case",
            type=CaseType.swe_pro_instance,
            status=CaseStatus.active,
        )
    )
    session.commit()
    service = RunService(session)
    service.create_run(
        RunCreate(
            run_id=run_id,
            config=RunConfigCreate(
                case_id="pro-case",
                prompt_variant="swe_default",
                executor_model="stub-executor",
            ),
        )
    )
    run = service.get_run(run_id)
    run.state = state
    session.add(run)
    session.commit()


def test_autoscore_swe_pro_uses_objective_when_conclusive():
    # SWE-bench Pro with a runnable test verdict scores from tests, like SWE-bench.
    with _session() as session:
        _seed_swe_pro_run(session, "pro-1", RunState.awaiting_manual_score)
        _store_validation(session, "pro-1", passed=True)
        _autoscore_run(session, "pro-1")
        run = RunService(session).get_run("pro-1")
        assert run.state == RunState.completed
        assert run.score == 10
        assert "Pro objective" in run.notes

    with _session() as session:
        _seed_swe_pro_run(session, "pro-2", RunState.awaiting_manual_score)
        _store_validation(session, "pro-2", passed=False)
        _autoscore_run(session, "pro-2")
        assert RunService(session).get_run("pro-2").score == 0


def test_autoscore_swe_pro_falls_back_to_llm_judge(tmp_path, monkeypatch):
    # No conclusive objective verdict → the audit LLM grades the diff instead of
    # parking the run in manual scoring.
    runs = tmp_path / "runs"
    monkeypatch.setattr(
        tasks_module, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs)
    )
    with _session() as session:
        _seed_swe_pro_run(session, "pro-3", RunState.awaiting_manual_score)
        (runs / "pro-3").mkdir(parents=True)
        (runs / "pro-3" / "prompt.md").write_text("Fix the TOML config loader.")
        (runs / "pro-3" / "diff.patch").write_text("diff --git a/c.py\n+toml branch")

        def fake_gateway(model: str, system: str, user: str) -> str:
            assert "Fix the TOML config loader" in user
            assert "toml branch" in user
            return '{"score": 7, "rationale": "solid partial fix"}'

        _autoscore_run(session, "pro-3", gateway=fake_gateway)
        run = RunService(session).get_run("pro-3")
        assert run.state == RunState.completed
        assert run.score == 7
        assert "LLM-judged" in run.notes
        validation = ValidationService(session).get_validation("pro-3")
        assert validation is not None
        assert validation.subjective["score_source"] == "llm_judge"


def test_autoscore_swe_pro_judge_failure_leaves_manual(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setattr(
        tasks_module, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs)
    )
    with _session() as session:
        _seed_swe_pro_run(session, "pro-4", RunState.awaiting_manual_score)
        (runs / "pro-4").mkdir(parents=True)
        (runs / "pro-4" / "diff.patch").write_text("diff")

        def boom(model: str, system: str, user: str) -> str:
            raise RuntimeError("gateway down")

        # Best-effort: a judge failure never crashes, run stays awaiting a manual score.
        _autoscore_run(session, "pro-4", gateway=boom)
        assert (
            RunService(session).get_run("pro-4").state == RunState.awaiting_manual_score
        )


def test_swe_continuation_prompt_targets_the_issue(tmp_path):
    spec = _swe_spec()
    last_eval = {
        "resolved": False,
        "f2p_passed": 0,
        "f2p_total": 1,
        "p2p_passed": 18,
        "p2p_total": 18,
    }
    path = bench_run.generate_swe_continuation_prompt(
        "swe-run-1", 2, tmp_path, spec, last_eval
    )
    text = path.read_text()
    assert "tests/test_config.py::test_config_from_file_toml" in text
    assert "resolved=False" in text
    assert "library source only" in text
    # The Tetris framing must be gone.
    assert "main.py" not in text.replace("NO main.py", "")
    assert "BENCH_SMOKE" not in text
    assert "milestone" not in text.lower()


def test_swe_step_evaluation_uses_evaluate(monkeypatch, tmp_path):
    import swebench_common

    calls = {}

    def fake_evaluate(repo_dir, python, f2p, p2p):
        calls["args"] = (repo_dir, python, f2p, p2p)
        return {
            "resolved": True,
            "f2p_passed": 1,
            "f2p_total": 1,
            "p2p_passed": 1,
            "p2p_total": 1,
            "per_test": {},
            "raw": "",
        }

    monkeypatch.setattr(swebench_common, "evaluate", fake_evaluate)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = bench_run.swe_step_evaluation(workspace, _swe_spec())
    assert result is not None
    assert result["resolved"] is True
    assert calls["args"][2] == _swe_spec()["fail_to_pass"]
    # Missing F2P list or workspace → None, never raises.
    assert bench_run.swe_step_evaluation(workspace, {"fail_to_pass": []}) is None
    assert bench_run.swe_step_evaluation(tmp_path / "nope", _swe_spec()) is None


def test_a_harness_mismatch_between_config_and_metadata_is_recorded(
    tmp_path, monkeypatch
):
    """Configured is what was asked for; metadata is what actually ran."""
    from harness_lab_app.models import RunEvent
    from harness_lab_app.tasks import _check_harness_provenance

    runs = tmp_path / "runs"
    monkeypatch.setattr(
        tasks_module, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs)
    )
    with _session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="mismatch-1",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                    harness="claude",
                ),
            )
        )
        (runs / "mismatch-1").mkdir(parents=True)
        (runs / "mismatch-1" / "metadata.json").write_text('{"harness": "opencode"}')

        _check_harness_provenance(session, "mismatch-1")
        events = session.exec(
            select(RunEvent).where(RunEvent.run_id == "mismatch-1")
        ).all()
        assert any("provenance mismatch" in event.message for event in events)


def test_a_matching_harness_records_nothing(tmp_path, monkeypatch):
    from harness_lab_app.models import RunEvent
    from harness_lab_app.tasks import _check_harness_provenance

    runs = tmp_path / "runs"
    monkeypatch.setattr(
        tasks_module, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs)
    )
    with _session() as session:
        RunService(session).create_run(
            RunCreate(
                run_id="match-1",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                    harness="claude",
                ),
            )
        )
        (runs / "match-1").mkdir(parents=True)
        (runs / "match-1" / "metadata.json").write_text('{"harness": "claude"}')

        _check_harness_provenance(session, "match-1")
        events = session.exec(
            select(RunEvent).where(RunEvent.run_id == "match-1")
        ).all()
        assert not any("provenance mismatch" in event.message for event in events)
