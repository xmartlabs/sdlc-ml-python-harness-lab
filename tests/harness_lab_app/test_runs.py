from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine, select

from harness_lab_app import run_service as run_service_module
from harness_lab_app.models import Artifact, Run, RunConfig, RunEvent, RunState
from harness_lab_app.run_service import (
    RunAlreadyExistsError,
    RunDeleteConflictError,
    RunPreflightError,
    RunService,
)
from harness_lab_app.schemas import RunConfigCreate, RunCreate


def _stub_config(**over) -> RunConfigCreate:
    base = {
        "case_id": "tetris-v1",
        "prompt_variant": "sdlc",
        "executor_model": "stub-executor",
    }
    base.update(over)
    return RunConfigCreate(**base)


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
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                ),
            )
        )

        assert run.state == RunState.pending
        assert "--case" in run.command


def test_command_includes_fresh_flag():
    # Web runs are managed: the runner must reset a stale workspace and archive
    # it afterwards (ADR-0006), signalled by --fresh from the adapter.
    config = RunConfigCreate(
        case_id="tetris-v1",
        prompt_variant="sdlc",
        executor_model="stub-executor",
    )
    with make_session() as session:
        command = RunService(session).preview_command("tetris-v1-K-001", config)
    assert "--fresh" in command


def test_preflight_rejects_empty_executor_model():
    with make_session() as session:
        service = RunService(session)
        with pytest.raises(RunPreflightError):
            service.create_run(
                RunCreate(
                    run_id="no-exec-1",
                    config=RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc"),
                )
            )


def test_preflight_allows_dry_run_without_executor():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="dry-no-exec-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
    assert run.state == RunState.pending


def test_preflight_rejects_placeholder_gateway_token(monkeypatch):
    monkeypatch.setattr(
        run_service_module,
        "read_env_file_value",
        lambda *_a, **_k: "sk-local-change-me",
    )
    with make_session() as session:
        service = RunService(session)
        with pytest.raises(RunPreflightError):
            service.create_run(
                RunCreate(
                    run_id="placeholder-token-1",
                    config=RunConfigCreate(
                        case_id="tetris-v1",
                        prompt_variant="sdlc",
                        executor_model="gateway/some-model",
                    ),
                )
            )


def test_preflight_allows_gateway_with_real_token(monkeypatch):
    monkeypatch.setattr(
        run_service_module, "read_env_file_value", lambda *_a, **_k: "sk-real-token"
    )
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="real-token-1",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="gateway/some-model",
                ),
            )
        )
    assert run.state == RunState.pending


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
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                executor_model="stub-executor",
            ),
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
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                ),
            )
        )

    assert enqueued == ["queued-run-1"]


def test_create_run_marks_failed_on_enqueue_failure():
    def broken_enqueue(run_id: str) -> None:
        raise ConnectionError("broker down")

    with make_session() as session:
        service = RunService(session, enqueue=broken_enqueue)
        run = service.create_run(
            RunCreate(
                run_id="queued-run-2",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                ),
            )
        )

        # A broker failure must not leave the run silently stuck in pending.
        assert run.state == RunState.failed
        assert run.failure_reason == "Queue broker unavailable — run not started"
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


def test_create_run_generates_auto_id_when_omitted():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(RunCreate(config=_stub_config(case_id="autoid-a")))
    assert run.run_id == "autoid-a-K-001"


def test_auto_id_increments_across_runs():
    with make_session() as session:
        service = RunService(session)
        first = service.create_run(RunCreate(config=_stub_config(case_id="autoid-b")))
        first_id = first.run_id
        second = service.create_run(RunCreate(config=_stub_config(case_id="autoid-b")))
        assert first_id == "autoid-b-K-001"
        assert second.run_id == "autoid-b-K-002"


def test_explicit_label_used_verbatim():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(label="my-custom-run", config=_stub_config(case_id="autoid-c"))
        )
    assert run.run_id == "my-custom-run"


def test_clone_run_reuses_config_with_new_id():
    with make_session() as session:
        service = RunService(session)
        source = service.create_run(
            RunCreate(config=_stub_config(case_id="autoid-d", max_steps=7))
        )
        source_id = source.run_id
        clone = service.clone_run(source_id)

        assert clone.run_id == "autoid-d-K-002"
        assert clone.run_id != source_id
        # Same arm: the cloned command carries the source's config (max steps etc.).
        assert "7" in clone.command


def test_delete_run_removes_run_and_dependent_rows():
    with make_session() as session:
        service = RunService(session)
        service.create_run(RunCreate(run_id="del-1", config=_stub_config()))
        session.add(Artifact(run_id="del-1", name="report.md", path="x"))
        session.commit()

        service.delete_run("del-1")

        assert session.exec(select(Run).where(Run.run_id == "del-1")).first() is None
        assert (
            session.exec(select(Artifact).where(Artifact.run_id == "del-1")).first()
            is None
        )
        assert not service.list_events("del-1")
        # The 1:1 RunConfig is removed too.
        assert session.exec(select(RunConfig)).first() is None


def test_delete_running_run_without_force_conflicts():
    with make_session() as session:
        service = RunService(session)
        service.create_run(RunCreate(run_id="del-run-2", config=_stub_config()))
        service.mark_running("del-run-2")

        with pytest.raises(RunDeleteConflictError):
            service.delete_run("del-run-2")

        assert service.get_run("del-run-2").state == RunState.running


def test_delete_running_run_with_force_succeeds():
    with make_session() as session:
        service = RunService(session)
        service.create_run(RunCreate(run_id="del-run-3", config=_stub_config()))
        service.mark_running("del-run-3")

        service.delete_run("del-run-3", force=True)

        assert (
            session.exec(select(Run).where(Run.run_id == "del-run-3")).first() is None
        )


def test_delete_missing_run_raises():
    with make_session() as session, pytest.raises(ValueError):
        RunService(session).delete_run("nope")


def test_delete_run_enqueues_container_cleanup():
    cleaned: list[str] = []
    with make_session() as session:
        service = RunService(session, enqueue_cleanup=cleaned.append)
        service.create_run(RunCreate(run_id="del-run-4", config=_stub_config()))

        service.delete_run("del-run-4")

    assert cleaned == ["del-run-4"]


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
