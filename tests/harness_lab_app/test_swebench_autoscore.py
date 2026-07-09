"""SWE-bench auto-scoring: the objective verdict completes the run by itself."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.case_service import CaseService
from harness_lab_app.models import RunState, Validation
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate
from harness_lab_app.tasks import _autoscore_objective_case

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
        _autoscore_objective_case(session, "swe-auto-1")
        run = RunService(session).get_run("swe-auto-1")
        assert run.state == RunState.completed
        assert run.score == 10
        assert "auto-scored" in run.notes


def test_autoscore_not_resolved_scores_0():
    with _session() as session:
        _seed_swe_run(session, "swe-auto-2", RunState.awaiting_manual_score)
        _store_validation(session, "swe-auto-2", passed=False)
        _autoscore_objective_case(session, "swe-auto-2")
        run = RunService(session).get_run("swe-auto-2")
        assert run.state == RunState.completed
        assert run.score == 0


def test_autoscore_skips_without_validation_and_wrong_state():
    with _session() as session:
        _seed_swe_run(session, "swe-auto-3", RunState.awaiting_manual_score)
        # No validation → stays awaiting (manual fallback).
        _autoscore_objective_case(session, "swe-auto-3")
        assert (
            RunService(session).get_run("swe-auto-3").state
            == RunState.awaiting_manual_score
        )

    with _session() as session:
        _seed_swe_run(session, "swe-auto-4", RunState.failed)
        _store_validation(session, "swe-auto-4", passed=True)
        # Terminal-but-not-awaiting runs are never rescored.
        _autoscore_objective_case(session, "swe-auto-4")
        assert RunService(session).get_run("swe-auto-4").state == RunState.failed


def test_autoscore_ignores_non_swe_cases():
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
        # Unknown case (not in DB) / non-SWE case → best-effort no-op.
        _autoscore_objective_case(session, "tetris-auto-1")
        assert service.get_run("tetris-auto-1").state == RunState.awaiting_manual_score


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
