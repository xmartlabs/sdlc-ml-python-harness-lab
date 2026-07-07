from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.models import RunEvent, RunState
from harness_lab_app.run_service import RunAlreadyExistsError, RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate


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


def test_create_run_rejects_auto_score_mode():
    # bench_run.py argparse only accepts manual|interactive|none; the web
    # flow standardizes on manual.
    with pytest.raises(ValidationError):
        RunCreate(
            run_id="tetris-v1-K-003",
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                score_mode="auto",
            ),
        )


def test_create_run_rejects_duplicate_run_id():
    with make_session() as session:
        service = RunService(session)
        payload = RunCreate(
            run_id="dup-run-1",
            config=RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc"),
        )
        service.create_run(payload)

        with pytest.raises(RunAlreadyExistsError):
            service.create_run(payload)


def test_dry_run_defaults_to_false():
    config = RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc")
    assert config.dry_run is False


def test_create_run_publishes_to_queue():
    enqueued: list[str] = []
    with make_session() as session:
        service = RunService(session, enqueue=enqueued.append)
        service.create_run(
            RunCreate(
                run_id="queued-run-1",
                config=RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc"),
            )
        )

    assert enqueued == ["queued-run-1"]


def test_create_run_survives_enqueue_failure():
    def broken_enqueue(run_id: str) -> None:
        raise ConnectionError("broker down")

    with make_session() as session:
        service = RunService(session, enqueue=broken_enqueue)
        run = service.create_run(
            RunCreate(
                run_id="queued-run-2",
                config=RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc"),
            )
        )

        assert run.state == RunState.pending
        messages = [
            event.message
            for event in service.list_events("queued-run-2", ascending=True)
        ]
        assert any("Failed to enqueue" in message for message in messages)


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
