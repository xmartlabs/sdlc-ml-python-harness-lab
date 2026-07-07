from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from harness_lab_app import tasks
from harness_lab_app.executors import SubprocessRunExecutor
from harness_lab_app.models import RunState
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate
from harness_lab_app.tasks import _execute_run_body, reconcile_stale_runs


def make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def create_pending_run(engine, run_id: str, command: list[str] | None = None):
    with Session(engine) as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id=run_id,
                config=RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc"),
            )
        )
        if command is not None:
            run = service.get_run(run_id)
            run.command = command
            session.add(run)
            session.commit()


class FakeHandle:
    def __init__(self, lines: list[tuple[str, str]], exit_code: int = 0):
        self._lines = lines
        self._exit_code = exit_code
        self.stopped = False

    def iter_lines(self) -> Iterator[tuple[str, str]]:
        yield from self._lines

    def stop(self) -> None:
        self.stopped = True

    def wait(self) -> int:
        return self._exit_code


class FakeExecutor:
    def __init__(self, handle: FakeHandle):
        self.handle = handle
        self.started_with: tuple[str, list[str]] | None = None

    def start(self, run_id: str, command: list[str]) -> FakeHandle:
        self.started_with = (run_id, command)
        return self.handle


def get_run_state(engine, run_id: str):
    with Session(engine) as session:
        service = RunService(session)
        run = service.get_run(run_id)
        return run.state, run.exit_code, run.failure_reason


def test_execute_run_success_reaches_awaiting_manual_score():
    engine = make_engine()
    create_pending_run(engine, "task-ok-1")
    executor = FakeExecutor(FakeHandle([("info", "hello"), ("info", "done")]))

    _execute_run_body("task-ok-1", executor, db_engine=engine)

    state, exit_code, failure = get_run_state(engine, "task-ok-1")
    assert state == RunState.awaiting_manual_score
    assert exit_code == 0
    assert failure is None
    assert executor.started_with is not None
    run_id, command = executor.started_with
    assert run_id == "task-ok-1"
    # Command is rebuilt from RunConfig at execution time.
    assert "--case" in command
    assert "tetris-v1" in command
    with Session(engine) as session:
        messages = [
            event.message
            for event in RunService(session).list_events("task-ok-1", ascending=True)
        ]
    assert "hello" in messages
    assert "done" in messages


def test_execute_run_failure_records_tail():
    engine = make_engine()
    create_pending_run(engine, "task-fail-1")
    executor = FakeExecutor(FakeHandle([("info", "boom happened")], exit_code=2))

    _execute_run_body("task-fail-1", executor, db_engine=engine)

    state, exit_code, failure = get_run_state(engine, "task-fail-1")
    assert state == RunState.failed
    assert exit_code == 2
    assert failure is not None
    assert "boom happened" in failure


def test_execute_run_skips_non_pending_runs():
    engine = make_engine()
    create_pending_run(engine, "task-skip-1")
    with Session(engine) as session:
        service = RunService(session)
        service.mark_running("task-skip-1")
        service.mark_finished("task-skip-1", 0)
    executor = FakeExecutor(FakeHandle([("info", "should-not-run")]))

    _execute_run_body("task-skip-1", executor, db_engine=engine)

    assert executor.started_with is None
    state, _, _ = get_run_state(engine, "task-skip-1")
    assert state == RunState.awaiting_manual_score


def test_execute_run_streams_real_subprocess_output_in_order():
    engine = make_engine()
    create_pending_run(engine, "task-stream-1")

    class FixedCommandExecutor(SubprocessRunExecutor):
        def start(self, run_id: str, command: list[str]):
            return super().start(
                run_id,
                [
                    "python3",
                    "-c",
                    "print('line-one'); print('line-two'); print('line-three')",
                ],
            )

    _execute_run_body("task-stream-1", FixedCommandExecutor(), db_engine=engine)

    state, exit_code, _ = get_run_state(engine, "task-stream-1")
    assert state == RunState.awaiting_manual_score
    assert exit_code == 0
    with Session(engine) as session:
        messages = [
            event.message
            for event in RunService(session).list_events(
                "task-stream-1", ascending=True
            )
        ]
    assert "line-one" in messages
    assert "line-three" in messages
    assert messages.index("line-one") < messages.index("line-three")


def test_execute_run_cancel_requested_terminates_run():
    engine = make_engine()
    create_pending_run(engine, "task-cancel-1")
    with Session(engine) as session:
        service = RunService(session)
        run = service.get_run("task-cancel-1")
        run.cancel_requested = True
        session.add(run)
        session.commit()

    class FixedCommandExecutor(SubprocessRunExecutor):
        def start(self, run_id: str, command: list[str]):
            return super().start(
                run_id,
                ["python3", "-c", "import time; time.sleep(30); print('late')"],
            )

    _execute_run_body("task-cancel-1", FixedCommandExecutor(), db_engine=engine)

    state, _, _ = get_run_state(engine, "task-cancel-1")
    assert state == RunState.cancelled
    with Session(engine) as session:
        messages = [
            event.message
            for event in RunService(session).list_events(
                "task-cancel-1", ascending=True
            )
        ]
    assert "late" not in messages


def test_execute_run_timeout_marks_failed(monkeypatch: pytest.MonkeyPatch):
    engine = make_engine()
    create_pending_run(engine, "task-timeout-1")

    class FakeSettings:
        run_timeout_seconds = 0

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())

    class FixedCommandExecutor(SubprocessRunExecutor):
        def start(self, run_id: str, command: list[str]):
            return super().start(
                run_id,
                ["python3", "-c", "import time; time.sleep(30)"],
            )

    _execute_run_body("task-timeout-1", FixedCommandExecutor(), db_engine=engine)

    state, _, failure = get_run_state(engine, "task-timeout-1")
    assert state == RunState.failed
    assert failure is not None
    assert "timed out" in failure


def test_execute_run_executor_error_marks_failed():
    engine = make_engine()
    create_pending_run(engine, "task-error-1")

    class BrokenExecutor:
        def start(self, run_id: str, command: list[str]):
            raise RuntimeError("docker daemon unreachable")

    _execute_run_body("task-error-1", BrokenExecutor(), db_engine=engine)

    state, exit_code, failure = get_run_state(engine, "task-error-1")
    assert state == RunState.failed
    assert exit_code == -1
    assert failure is not None
    assert "docker daemon unreachable" in failure


class FakeReconcileContainers:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    def get(self, name: str) -> Any:
        raise RuntimeError(f"No such container: {name}")


class FakeReconcileClient:
    def __init__(self) -> None:
        self.containers = FakeReconcileContainers()


def test_reconcile_marks_orphaned_running_runs_failed():
    engine = make_engine()
    create_pending_run(engine, "task-orphan-1")
    with Session(engine) as session:
        RunService(session).mark_running("task-orphan-1")

    reconciled = reconcile_stale_runs(client=FakeReconcileClient(), db_engine=engine)

    assert reconciled == 1
    state, exit_code, failure = get_run_state(engine, "task-orphan-1")
    assert state == RunState.failed
    assert exit_code == -1
    assert failure is not None
    assert "retry to resume" in failure


def test_reconcile_ignores_finished_runs():
    engine = make_engine()
    create_pending_run(engine, "task-done-1")
    with Session(engine) as session:
        service = RunService(session)
        service.mark_running("task-done-1")
        service.mark_finished("task-done-1", 0)

    reconciled = reconcile_stale_runs(client=FakeReconcileClient(), db_engine=engine)

    assert reconciled == 0
    state, _, _ = get_run_state(engine, "task-done-1")
    assert state == RunState.awaiting_manual_score
