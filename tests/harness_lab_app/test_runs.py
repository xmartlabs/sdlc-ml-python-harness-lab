from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.models import RunEvent, RunState
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate
from harness_lab_app.worker import run_pending_once


def make_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_command_preview_uses_bench_run():
    config = RunConfigCreate(
        case_id="tetris-v1",
        prompt_variant="sdlc",
        executor_model="gateway/nim-nemotron-super-120b",
        max_steps=10,
        dry_run=True,
    )

    with make_session() as session:
        service = RunService(session)
        command = service.preview_command("tetris-v1-J-002", config)

    assert command[:4] == ["uv", "run", "python", "scripts/bench_run.py"]
    assert "--dry-run" in command
    assert "tetris-v1-J-002" in command


def test_create_run_starts_pending():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="tetris-v1-J-002",
                config=RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc"),
            )
        )

        assert run.state == RunState.pending
        assert "--case" in run.command


def test_create_run_rejects_interactive_score_mode():
    with pytest.raises(ValidationError):
        RunCreate(
            run_id="tetris-v1-K-001",
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                score_mode="interactive",
            ),
        )


def test_create_run_rejects_unknown_score_mode():
    with pytest.raises(ValidationError):
        RunCreate(
            run_id="tetris-v1-K-002",
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                score_mode="anything",
            ),
        )


def test_worker_finishes_dry_run():
    with make_session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="dry-run-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )

        processed = run_pending_once(session, execute_process=False)
        run = service.get_run("dry-run-1")

        assert processed == 1
        assert run.state == RunState.awaiting_manual_score
        assert (
            session.query(RunEvent).filter(RunEvent.run_id == "dry-run-1").count() >= 2
        )


def test_worker_streams_process_output_line_by_line():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="stream-run-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        run.command = [
            "python3",
            "-c",
            "print('line-one'); print('line-two'); print('line-three')",
        ]
        session.add(run)
        session.commit()

        processed = run_pending_once(session, execute_process=True)
        finished = service.get_run("stream-run-1")

        assert processed == 1
        assert finished.state == RunState.awaiting_manual_score
        assert finished.exit_code == 0

        messages = [
            event.message
            for event in service.list_events("stream-run-1", ascending=True)
        ]
        assert "line-one" in messages
        assert "line-two" in messages
        assert "line-three" in messages
        assert messages.index("line-one") < messages.index("line-three")


def test_cancel_requested_terminates_running_process():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="cancel-run-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        run.command = [
            "python3",
            "-c",
            "import time; time.sleep(5); print('should-not-print')",
        ]
        run.cancel_requested = True
        session.add(run)
        session.commit()

        processed = run_pending_once(session, execute_process=True)
        finished = service.get_run("cancel-run-1")

        assert processed == 1
        assert finished.state == RunState.cancelled
        assert finished.cancel_requested is False
        messages = [
            event.message
            for event in service.list_events("cancel-run-1", ascending=True)
        ]
        assert "should-not-print" not in messages


def test_cancel_run_signals_running_process_instead_of_overwriting_state():
    with make_session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="cancel-signal-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        service.mark_running("cancel-signal-1")

        cancelled = service.cancel_run("cancel-signal-1")

        assert cancelled.state == RunState.running
        assert cancelled.cancel_requested is True


def test_retry_run_rebuilds_command_with_resume():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="retry-run-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        service.mark_running("retry-run-1")
        service.mark_finished("retry-run-1", 1, "boom")

        retried = service.retry_run("retry-run-1")

        assert retried.state == RunState.pending
        assert retried.exit_code is None
        assert retried.failure_reason is None
        assert "--resume" in retried.command
        assert run.run_id == retried.run_id


def test_score_run_updates_fields_and_state():
    with make_session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="score-run-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        service.mark_running("score-run-1")
        service.mark_finished("score-run-1", 0)

        scored = service.score_run("score-run-1", 9, 2, "Manual QA passed")

        assert scored.state == RunState.completed
        assert scored.score == 9
        assert scored.interventions == 2
        assert scored.notes == "Manual QA passed"


def test_list_events_ascending_orders_chronologically():
    with make_session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="ordered-run-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        session.add(RunEvent(run_id="ordered-run-1", message="first"))
        session.add(RunEvent(run_id="ordered-run-1", message="second"))
        session.commit()

        ascending = service.list_events("ordered-run-1", ascending=True)
        descending = service.list_events("ordered-run-1")

        assert [event.message for event in ascending][-2:] == ["first", "second"]
        assert [event.message for event in descending][:2] == ["second", "first"]
