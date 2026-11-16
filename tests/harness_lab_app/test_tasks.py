from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from harness_lab_app import case_service, tasks
from harness_lab_app.executors import SubprocessRunExecutor
from harness_lab_app.models import HarnessSource, RunEvent, RunState
from harness_lab_app.run_failures import is_unmeasured
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate
from harness_lab_app.tasks import InFlight, _execute_run_body, reconcile_stale_runs


class RunSettings:
    """Only the settings ``_execute_run_body`` reads, with inert defaults.

    Each test subclasses this and overrides the one clock it is exercising.
    ``data_runs_dir`` is filled in per test by :func:`_stage_dir`: the worker
    stages ``case_spec.json`` for every case type now, so even a run whose case
    row is absent touches the run directory.
    """

    run_timeout_seconds = 3600
    run_stall_seconds = 3600
    run_model_wait_seconds = 3600
    run_max_stream_errors = 0
    data_runs_dir = Path("/nonexistent")


@pytest.fixture(autouse=True)
def _stage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RunSettings, "data_runs_dir", tmp_path / "runs")


def make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def create_pending_run(
    engine,
    run_id: str,
    command: list[str] | None = None,
    harness_source: HarnessSource = HarnessSource.lab_root,
    harness: str = "opencode",
):
    with Session(engine) as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id=run_id,
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                    harness=harness,
                    harness_source=harness_source,
                ),
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
        self.start_kwargs: dict = {}

    def start(self, run_id: str, command: list[str], **kwargs) -> FakeHandle:
        self.started_with = (run_id, command)
        self.start_kwargs = kwargs
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


def test_a_lab_root_run_asks_the_executor_to_mask_nothing():
    """The lab's harness *is* the treatment there — hiding it would be the bug."""
    engine = make_engine()
    create_pending_run(engine, "task-mask-none")
    executor = FakeExecutor(FakeHandle([("info", "done")]))

    _execute_run_body("task-mask-none", executor, db_engine=engine)

    # No governance mask. `hidden` is the agent container's grading mask
    # (ADR-0079): the lab's calibration references are hidden in every arm.
    assert executor.start_kwargs == {"hidden": []}


def test_a_bare_run_asks_the_executor_to_mask_everything():
    """A bare arm is bare only because the container hides the lab (ADR-0049)."""
    engine = make_engine()
    create_pending_run(engine, "task-mask-all", harness_source=HarnessSource.bare)
    executor = FakeExecutor(FakeHandle([("info", "done")]))

    _execute_run_body("task-mask-all", executor, db_engine=engine)

    assert executor.start_kwargs == {"governance_mask": "all", "hidden": []}


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
        def start(self, run_id: str, command: list[str], **kwargs):
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
        def start(self, run_id: str, command: list[str], **kwargs):
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

    class FakeSettings(RunSettings):
        run_timeout_seconds = 0
        run_stall_seconds = 0

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())

    class FixedCommandExecutor(SubprocessRunExecutor):
        def start(self, run_id: str, command: list[str], **kwargs):
            return super().start(
                run_id,
                ["python3", "-c", "import time; time.sleep(30)"],
            )

    _execute_run_body("task-timeout-1", FixedCommandExecutor(), db_engine=engine)

    state, _, failure = get_run_state(engine, "task-timeout-1")
    assert state == RunState.failed
    assert failure is not None
    assert "timed out" in failure


def test_execute_run_stall_marks_failed(monkeypatch: pytest.MonkeyPatch):
    """Run tetris-v1-K-034: alive, silent, and going nowhere.

    The agent shelled out to a pygame process that caught SIGTERM and never
    died; because the surviving grandchild held the stdout pipe open, opencode
    blocked forever reading it. 47 minutes without one byte of output, state
    "running" in the UI, and the 2-hour total timeout was the only thing that
    would ever have ended it. A run that has stopped talking is stuck, and
    stuck is a failure to report now, not in two hours.
    """
    engine = make_engine()
    create_pending_run(engine, "task-stall-1")

    class FakeSettings(RunSettings):
        run_timeout_seconds = 3600  # nowhere near — the stall clock must fire
        run_stall_seconds = 1

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())

    class SilentExecutor(SubprocessRunExecutor):
        def start(self, run_id: str, command: list[str], **kwargs):
            # Speaks once, then goes quiet without exiting — a hung tool call.
            return super().start(
                run_id,
                [
                    "python3",
                    "-c",
                    "print('starting milestone 3', flush=True); "
                    "import time; time.sleep(60)",
                ],
            )

    _execute_run_body("task-stall-1", SilentExecutor(), db_engine=engine)

    state, _, failure = get_run_state(engine, "task-stall-1")
    assert state == RunState.failed
    assert failure is not None
    assert "stalled" in failure
    # The last thing it said travels with the verdict: that line is normally
    # the tool call it is stuck inside, and the first thing anyone would read.
    assert "starting milestone 3" in failure


def test_a_talking_run_is_never_treated_as_stalled(monkeypatch: pytest.MonkeyPatch):
    """Output resets the clock, so a slow-but-alive run finishes normally."""
    engine = make_engine()
    create_pending_run(engine, "task-chatty-1")

    class FakeSettings(RunSettings):
        run_timeout_seconds = 3600
        run_stall_seconds = 2

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())

    class ChattyExecutor(SubprocessRunExecutor):
        def start(self, run_id: str, command: list[str], **kwargs):
            return super().start(
                run_id,
                [
                    "python3",
                    "-c",
                    "import time\n"
                    "for i in range(6):\n"
                    "    print(f'step {i}', flush=True)\n"
                    "    time.sleep(0.5)\n",
                ],
            )

    _execute_run_body("task-chatty-1", ChattyExecutor(), db_engine=engine)

    state, exit_code, failure = get_run_state(engine, "task-chatty-1")
    # A manual-score case lands here rather than `completed`; what matters is
    # that it reached its own ending instead of being killed as stalled.
    assert state == RunState.awaiting_manual_score
    assert exit_code == 0
    assert failure in (None, "")


# Verbatim opencode output. The watchdog decides what a silence means by
# prefix-matching these, so a paraphrase would prove nothing.
STREAM_START_LINE = (
    "timestamp=2026-08-13T17:08:29.555Z level=INFO run=0aa06ee9 message=stream "
    "providerID=lmstudio modelID=qwen/qwen3.6-35b-a3b"
)
STREAM_ERROR_LINE = (
    "timestamp=2026-08-11T22:23:08.812Z level=ERROR run=1cf7bf13 "
    'message="stream error" providerID=gateway modelID=nim-nemotron-super-120b '
    'error.error="AI_APICallError: Cannot connect to API: Unable to connect."'
)


def _script_executor(program: str):
    """A SubprocessRunExecutor that runs `program` instead of the real command."""

    class ScriptExecutor(SubprocessRunExecutor):
        def start(self, run_id: str, command: list[str], **kwargs):
            return super().start(run_id, ["python3", "-c", program])

    return ScriptExecutor()


def test_a_model_still_processing_the_prompt_is_not_a_stall(
    monkeypatch: pytest.MonkeyPatch,
):
    """Run tetris-v1-K-050, and the reason the 35B "stopped working".

    The agent read its governance files, entered its first turn, and went quiet
    at `message=stream` — waiting for the first token. Ten minutes later the
    single silence clock killed it and the lab filed it as the agent wedging
    itself in a tool call. It was neither wedged nor at fault: that box needs
    318s of prompt processing before the first token of a 24k-token prompt, so
    the threshold was simply below the model's prefill time. Every 35B run died
    in this exact spot while the faster 9B never came close.
    """
    engine = make_engine()
    create_pending_run(engine, "task-prefill-1")

    class FakeSettings(RunSettings):
        run_timeout_seconds = 3600
        run_stall_seconds = 1  # a tool call gets one second here...
        run_model_wait_seconds = 3600  # ...but the model gets an hour
        run_max_stream_errors = 0

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())

    program = (
        f"import time\nprint({STREAM_START_LINE!r}, flush=True)\n"
        "time.sleep(3)\nprint('first token at last', flush=True)\n"
    )

    _execute_run_body("task-prefill-1", _script_executor(program), db_engine=engine)

    state, exit_code, failure = get_run_state(engine, "task-prefill-1")
    assert state == RunState.awaiting_manual_score
    assert exit_code == 0
    assert failure in (None, "")


def test_a_model_that_never_answers_is_stopped_without_blaming_it(
    monkeypatch: pytest.MonkeyPatch,
):
    """The model-wait budget still ends the run — it just says so honestly."""
    engine = make_engine()
    create_pending_run(engine, "task-prefill-2")

    class FakeSettings(RunSettings):
        run_timeout_seconds = 3600
        run_stall_seconds = 3600
        run_model_wait_seconds = 1
        run_max_stream_errors = 0

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())

    program = f"import time\nprint({STREAM_START_LINE!r}, flush=True)\ntime.sleep(60)\n"

    _execute_run_body("task-prefill-2", _script_executor(program), db_engine=engine)

    state, _, failure = get_run_state(engine, "task-prefill-2")
    assert state == RunState.failed
    assert failure is not None
    assert failure.startswith("No model response:")
    # Not a stall: the agent was not the one that stopped talking.
    assert "stalled" not in failure
    # And it names the knob, because only the operator can decide whether the
    # model is too slow to be worth it or the budget is too tight.
    assert "run_model_wait_seconds" in failure
    assert is_unmeasured(failure) is True


def test_a_failing_endpoint_is_given_up_on_instead_of_retried_for_two_hours(
    monkeypatch: pytest.MonkeyPatch,
):
    """Run tetris-v1-K-029: 123 failed requests in 70 minutes, filed as a timeout.

    opencode retries a failing stream forever, so the run burned its whole
    budget on an endpoint it could not reach and the leaderboard recorded it as
    nemotron attempting the case. Five failures in a row is already the answer.
    """
    engine = make_engine()
    create_pending_run(engine, "task-endpoint-1")

    class FakeSettings(RunSettings):
        run_timeout_seconds = 3600
        run_stall_seconds = 3600
        run_model_wait_seconds = 3600
        run_max_stream_errors = 3

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())

    # The real retry loop: setup line, failure, setup line, failure... Nothing
    # between them is progress, so nothing between them may reset the count.
    program = (
        "import time\n"
        "for _ in range(10):\n"
        f"    print({STREAM_START_LINE!r}, flush=True)\n"
        f"    print({STREAM_ERROR_LINE!r}, flush=True)\n"
        "time.sleep(60)\n"
    )

    _execute_run_body("task-endpoint-1", _script_executor(program), db_engine=engine)

    state, _, failure = get_run_state(engine, "task-endpoint-1")
    assert state == RunState.failed
    assert failure is not None
    assert failure.startswith("Model endpoint failing:")
    # The endpoint's own words travel with the verdict.
    assert "Cannot connect to API" in failure
    assert is_unmeasured(failure) is True


def test_a_run_no_model_ever_answered_is_not_a_completed_attempt():
    """Run instance_qutebrowser…-K-003: completed, score 0, and the model never spoke.

    Both provider requests died in two seconds on a gateway config error
    (`max_tokens` above the model's completion cap) — below the consecutive-
    error threshold, so no watchdog verdict formed. opencode gave up, but
    bench_run.py treated the step failure as survivable, carried on to scoring,
    archived an empty diff and exited 0. The run went on the leaderboard as
    "gpt-4o-mini attempted the case and resolved nothing", when it was never
    heard from at all.
    """
    engine = make_engine()
    create_pending_run(engine, "task-no-answer-1")
    executor = FakeExecutor(
        FakeHandle(
            [
                ("info", STREAM_START_LINE),
                ("error", STREAM_ERROR_LINE),
                ("info", STREAM_START_LINE),
                ("error", STREAM_ERROR_LINE),
                # bench_run.py carrying on regardless, exactly as K-003 did.
                ("info", "opencode exited with code 1 — see steps/step-1.log"),
                ("info", "[3/5] Scoring..."),
                ("info", "[5/5] Archiving workspace (managed run)..."),
                ("info", "diff.patch: 0 lines"),
            ],
            exit_code=0,
        )
    )

    _execute_run_body("task-no-answer-1", executor, db_engine=engine)

    state, exit_code, failure = get_run_state(engine, "task-no-answer-1")
    assert state == RunState.failed
    assert exit_code != 0
    assert failure is not None
    assert failure.startswith("Model endpoint failing:")
    # The provider's own words travel with the verdict.
    assert "Cannot connect to API" in failure
    assert is_unmeasured(failure) is True


def test_a_run_where_a_later_request_succeeds_keeps_its_result():
    """One answered request is a measurement; only zero-for-zero is void."""
    engine = make_engine()
    create_pending_run(engine, "task-recovered-1")
    executor = FakeExecutor(
        FakeHandle(
            [
                ("info", STREAM_START_LINE),
                ("error", STREAM_ERROR_LINE),
                ("info", STREAM_START_LINE),
                ("info", "→ Write main.py"),
            ],
            exit_code=0,
        )
    )

    _execute_run_body("task-recovered-1", executor, db_engine=engine)

    state, exit_code, failure = get_run_state(engine, "task-recovered-1")
    assert state == RunState.awaiting_manual_score
    assert exit_code == 0
    assert failure in (None, "")


def test_a_failure_the_run_recovers_from_does_not_end_it(
    monkeypatch: pytest.MonkeyPatch,
):
    """One bad request between two good turns is noise, not a dead endpoint."""
    engine = make_engine()
    create_pending_run(engine, "task-endpoint-2")

    class FakeSettings(RunSettings):
        run_timeout_seconds = 3600
        run_stall_seconds = 3600
        run_model_wait_seconds = 3600
        run_max_stream_errors = 2

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())

    program = (
        "for _ in range(5):\n"
        f"    print({STREAM_ERROR_LINE!r}, flush=True)\n"
        "    print('→ Write main.py', flush=True)\n"
    )

    _execute_run_body("task-endpoint-2", _script_executor(program), db_engine=engine)

    state, exit_code, failure = get_run_state(engine, "task-endpoint-2")
    assert state == RunState.awaiting_manual_score
    assert exit_code == 0
    assert failure in (None, "")


def test_execute_run_executor_error_marks_failed():
    engine = make_engine()
    create_pending_run(engine, "task-error-1")

    class BrokenExecutor:
        def start(self, run_id: str, command: list[str], **kwargs):
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


def _answered(*run_ids: str, lanes: tuple[str, ...] = ("runs", "runs-parallel")):
    """An inspection every run lane answered, executing ``run_ids``."""
    return InFlight(frozenset(run_ids), frozenset(lanes))


def test_reconcile_marks_orphaned_running_runs_failed():
    engine = make_engine()
    create_pending_run(engine, "task-orphan-1")
    with Session(engine) as session:
        RunService(session).mark_running("task-orphan-1")

    reconciled = reconcile_stale_runs(
        client=FakeReconcileClient(), db_engine=engine, in_flight=_answered()
    )

    assert reconciled == 1
    state, exit_code, failure = get_run_state(engine, "task-orphan-1")
    assert state == RunState.failed
    assert exit_code == -1
    assert failure is not None
    assert "retry to resume" in failure


def test_reconcile_spares_a_run_another_live_worker_is_executing():
    """Three workers now (ADR-0082): one restarting must not kill another's run.

    Every worker reconciles on boot. With one worker any `running` row was an
    orphan; with a second lane and an interactive worker, a restart of either
    would fail — and stop the container of — a run the other is still driving.
    """
    engine = make_engine()
    for run_id in ("task-live-1", "task-orphan-2"):
        create_pending_run(engine, run_id)
        with Session(engine) as session:
            RunService(session).mark_running(run_id)

    reconciled = reconcile_stale_runs(
        client=FakeReconcileClient(),
        db_engine=engine,
        in_flight=_answered("task-live-1"),
    )

    assert reconciled == 1
    assert get_run_state(engine, "task-live-1")[0] == RunState.running
    assert get_run_state(engine, "task-orphan-2")[0] == RunState.failed


def test_reconcile_touches_nothing_when_the_workers_cannot_be_asked(monkeypatch):
    """Unknown is not empty: failing every `running` row would kill the other lane."""
    from harness_lab_app import tasks

    engine = make_engine()
    create_pending_run(engine, "task-unknown-1")
    with Session(engine) as session:
        RunService(session).mark_running("task-unknown-1")
    monkeypatch.setattr(tasks, "runs_in_flight", lambda: None)

    assert reconcile_stale_runs(client=FakeReconcileClient(), db_engine=engine) == 0
    assert get_run_state(engine, "task-unknown-1")[0] == RunState.running


def test_a_lane_that_did_not_answer_keeps_its_runs():
    """A partial reply is complete only for the lanes in it.

    `inspect().active()` returns the workers that replied in time. If the
    second lane is slow to answer, its running row must not read as an orphan.
    """
    engine = make_engine()
    for run_id, lane in (
        ("task-main-orphan", "runs"),
        ("task-second", "runs-parallel"),
    ):
        create_pending_run(engine, run_id)
        with Session(engine) as session:
            RunService(session).mark_running(run_id, lane=lane)

    reconciled = reconcile_stale_runs(
        client=FakeReconcileClient(),
        db_engine=engine,
        in_flight=_answered(lanes=("runs",)),
    )

    assert reconciled == 1
    assert get_run_state(engine, "task-main-orphan")[0] == RunState.failed
    assert get_run_state(engine, "task-second")[0] == RunState.running


def test_a_run_from_before_the_second_lane_belongs_to_the_main_one():
    """No lane recorded: it ran on `runs`, and only that lane may judge it."""
    engine = make_engine()
    create_pending_run(engine, "task-old")
    with Session(engine) as session:
        RunService(session).mark_running("task-old")

    assert (
        reconcile_stale_runs(
            client=FakeReconcileClient(),
            db_engine=engine,
            in_flight=_answered(lanes=("runs-parallel",)),
        )
        == 0
    )
    assert get_run_state(engine, "task-old")[0] == RunState.running


def test_a_lone_worker_still_recovers_its_own_orphans_on_boot(monkeypatch):
    """The host mode runs one worker, which cannot answer while it boots.

    Read as "no lane answered", the restart would never fail a run it left
    behind. The booting worker answers for its own lanes.
    """
    from harness_lab_app import tasks

    engine = make_engine()
    create_pending_run(engine, "task-host-orphan")
    with Session(engine) as session:
        RunService(session).mark_running("task-host-orphan", lane="runs")
    monkeypatch.setattr(tasks, "runs_in_flight", lambda: None)

    reconciled = reconcile_stale_runs(
        client=FakeReconcileClient(), db_engine=engine, own_lanes=frozenset({"runs"})
    )

    assert reconciled == 1
    assert get_run_state(engine, "task-host-orphan")[0] == RunState.failed


def test_the_booting_worker_knows_its_own_lanes():
    from types import SimpleNamespace

    from harness_lab_app import tasks

    sender = SimpleNamespace(
        app=SimpleNamespace(
            amqp=SimpleNamespace(
                queues=SimpleNamespace(consume_from={"runs": 1, "interactive": 2})
            )
        )
    )
    assert tasks._own_lanes(sender) == frozenset({"runs"})
    assert tasks._own_lanes(None) == frozenset()


def _inspector(active, queues):
    class Inspect:
        def active(self):
            return active

        def active_queues(self):
            return queues

    return Inspect()


def test_active_tasks_are_read_whether_celery_reports_args_as_a_list_or_a_string(
    monkeypatch,
):
    from harness_lab_app import tasks

    active = {
        "runs@a": [
            {"name": "harness_lab_app.tasks.execute_run", "args": ["chess3d-v1-K-020"]}
        ],
        "parallel@b": [
            {
                "name": "harness_lab_app.tasks.execute_run",
                "args": "['chess3d-v1-K-021']",
            },
            {"name": "harness_lab_app.tasks.start_sandbox", "args": ["x"]},
        ],
        "interactive@c": [],
    }
    queues = {
        "runs@a": [{"name": "runs"}],
        "parallel@b": [{"name": "runs-parallel"}],
        "interactive@c": [{"name": "interactive"}],
    }
    monkeypatch.setattr(
        tasks.celery_app.control, "inspect", lambda **_kw: _inspector(active, queues)
    )
    reply = tasks.runs_in_flight()
    assert reply == InFlight(
        frozenset({"chess3d-v1-K-020", "chess3d-v1-K-021"}),
        frozenset({"runs", "runs-parallel"}),
    )


def test_a_worker_counts_for_its_lane_only_if_both_answers_came_back(monkeypatch):
    """Active list from one worker, queues from another: neither lane is complete."""
    from harness_lab_app import tasks

    active = {"runs@a": []}
    queues = {"parallel@b": [{"name": "runs-parallel"}]}
    monkeypatch.setattr(
        tasks.celery_app.control, "inspect", lambda **_kw: _inspector(active, queues)
    )
    assert tasks.runs_in_flight() == InFlight(frozenset(), frozenset())


def test_no_reply_from_any_worker_is_unknown_not_empty(monkeypatch):
    from harness_lab_app import tasks

    monkeypatch.setattr(
        tasks.celery_app.control, "inspect", lambda **_kw: _inspector(None, None)
    )
    assert tasks.runs_in_flight() is None


def test_reconcile_ignores_finished_runs():
    engine = make_engine()
    create_pending_run(engine, "task-done-1")
    with Session(engine) as session:
        service = RunService(session)
        service.mark_running("task-done-1")
        service.mark_finished("task-done-1", 0)

    reconciled = reconcile_stale_runs(
        client=FakeReconcileClient(), db_engine=engine, in_flight=_answered()
    )

    assert reconciled == 0
    state, _, _ = get_run_state(engine, "task-done-1")
    assert state == RunState.awaiting_manual_score


def test_case_spec_is_written_for_a_case_that_only_exists_in_the_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A case authored in the browser has no benchmarks/<id>/task.yaml.

    Without a spec the runner exits with "Unknown case" milliseconds after the
    run is queued, which is exactly what a UI-created case used to do.
    """
    import json
    from types import SimpleNamespace

    from harness_lab_app.models import Case, CaseType

    engine = make_engine()
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path, repo_root=tmp_path),
    )
    with Session(engine) as session:
        session.add(
            Case(
                case_id="doom-like",
                name="Doom-like Game",
                type=CaseType.multi_step,
                prompt_variants={"default": "Build a Doom-like game."},
                step_prompts=["Set up the loop.", "Add raycasting."],
                constraints=["Pure Python."],
                success_criteria={"runs": "The game starts."},
                max_steps=3,
            )
        )
        session.commit()
        tasks._write_case_spec(session, "doom-run-1", "doom-like", "default")

    spec = json.loads((tmp_path / "doom-run-1" / "case_spec.json").read_text())
    assert spec["case_type"] == "multi_step"
    assert spec["case_id"] == "doom-like"
    assert spec["prompt"] == "Build a Doom-like game."
    assert spec["step_prompts"] == ["Set up the loop.", "Add raycasting."]
    assert spec["max_steps"] == 3
    assert spec["constraints"] == ["Pure Python."]
    assert spec["success_criteria"] == {"runs": "The game starts."}
    # Empty repo is what tells the runner there is nothing to clone.
    assert spec["repo"] == ""


def test_a_prompt_stored_as_a_path_reaches_the_runner_as_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Cases imported from task.yaml store a repo-relative path, not a prompt.

    The runner container has no benchmark layout knowledge, so the worker — the
    process that can read the repo — resolves it.
    """
    import json
    from types import SimpleNamespace

    from harness_lab_app.models import Case, CaseType

    engine = make_engine()
    prompt_file = tmp_path / "benchmarks" / "tetris" / "prompt_naive.md"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text("Build Tetris.\nNo libraries.\n")
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path / "runs", repo_root=tmp_path),
    )
    # The actual path resolution runs in case_service (shared with the
    # prompt-preview API), so it needs the same fake repo_root.
    monkeypatch.setattr(
        case_service, "get_settings", lambda: SimpleNamespace(repo_root=tmp_path)
    )
    with Session(engine) as session:
        session.add(
            Case(
                case_id="tetris-v1",
                name="Tetris",
                type=CaseType.build_from_scratch,
                prompt_variants={"naive": "benchmarks/tetris/prompt_naive.md"},
            )
        )
        session.commit()
        tasks._write_case_spec(session, "tetris-run-2", "tetris-v1", "naive")

    spec = json.loads(
        (tmp_path / "runs" / "tetris-run-2" / "case_spec.json").read_text()
    )
    assert spec["prompt"] == "Build Tetris.\nNo libraries.\n"


def test_an_unresolvable_prompt_path_is_passed_through_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A one-line prompt is a prompt, even when it looks like a path."""
    import json
    from types import SimpleNamespace

    from harness_lab_app.models import Case, CaseType

    engine = make_engine()
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path, repo_root=tmp_path),
    )
    monkeypatch.setattr(
        case_service, "get_settings", lambda: SimpleNamespace(repo_root=tmp_path)
    )
    with Session(engine) as session:
        session.add(
            Case(
                case_id="terse",
                name="Terse",
                type=CaseType.single_prompt,
                prompt_variants={"default": "Fix src/app.py"},
            )
        )
        session.commit()
        tasks._write_case_spec(session, "terse-run-1", "terse", "default")

    spec = json.loads((tmp_path / "terse-run-1" / "case_spec.json").read_text())
    assert spec["prompt"] == "Fix src/app.py"


def test_case_spec_for_a_continuation_swaps_the_prompt_and_drops_step_prompts(
    tmp_path, monkeypatch
):
    """A continuation replaces the instruction, not the case (ADR-0026).

    The canned step prompts scripted the original attempt; replaying them would
    talk past the state the inherited workspace is actually in.
    """
    import json
    from types import SimpleNamespace

    from harness_lab_app.models import Case, CaseType

    engine = make_engine()
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path, repo_root=tmp_path),
    )
    with Session(engine) as session:
        session.add(
            Case(
                case_id="doom-like",
                name="Doom-like Game",
                type=CaseType.multi_step,
                prompt_variants={"default": "Build a Doom-like game."},
                step_prompts=["Set up the loop.", "Add raycasting."],
                success_criteria={"runs": "The game starts."},
                max_steps=3,
            )
        )
        session.commit()
        tasks._write_case_spec(
            session,
            "doom-run-1-C-001",
            "doom-like",
            "default",
            continuation_prompt="Now add a pause menu.",
            parent_run_id="doom-run-1",
        )

    spec = json.loads((tmp_path / "doom-run-1-C-001" / "case_spec.json").read_text())
    assert spec["prompt"] == "Now add a pause menu."
    assert spec["continues_run"] == "doom-run-1"
    assert spec["step_prompts"] == []
    # The task being measured is unchanged.
    assert spec["success_criteria"] == {"runs": "The game starts."}


def test_seed_continuation_workspace_prefers_the_archive(tmp_path, monkeypatch):
    from types import SimpleNamespace

    settings = SimpleNamespace(
        data_runs_dir=tmp_path / "data" / "runs",
        workspace_dir=tmp_path / "workspace",
        repo_root=tmp_path,
    )
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    # continuation_source_dir lives in run_service and reads its own settings.
    monkeypatch.setattr("harness_lab_app.run_service.get_settings", lambda: settings)

    archive = tmp_path / "data" / "runs" / "parent-1" / "workspace"
    archive.mkdir(parents=True)
    (archive / "main.py").write_text("print('archived')\n")
    live = tmp_path / "workspace" / "parent-1"
    live.mkdir(parents=True)
    (live / "main.py").write_text("print('stale live copy')\n")

    tasks._seed_continuation_workspace("parent-1-C-001", "parent-1")

    seeded = tmp_path / "workspace" / "parent-1-C-001" / "main.py"
    assert seeded.read_text() == "print('archived')\n"


def test_seed_continuation_workspace_fails_loudly_with_nothing_to_copy(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    settings = SimpleNamespace(
        data_runs_dir=tmp_path / "data" / "runs",
        workspace_dir=tmp_path / "workspace",
        repo_root=tmp_path,
    )
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr("harness_lab_app.run_service.get_settings", lambda: settings)

    with pytest.raises(RuntimeError) as excinfo:
        tasks._seed_continuation_workspace("gone-C-001", "gone")
    assert "workspace is gone" in str(excinfo.value)


def test_a_harness_that_died_leaves_no_capability_score(
    monkeypatch: pytest.MonkeyPatch,
):
    """A non-zero exit must not reach the scorer (ADR-0046).

    Until `bench_run` propagated its `harness_error`, a dead SSE connection
    archived at exit 0 satisfied the success path above: the run was recorded
    completed and auto-scored 0, and the corpus read a broken connection as a
    model that could not solve the case. The repair is not the state string —
    it is that objective validation and auto-scoring sit behind `failure is
    None`, so no `task_validation_pass` is fabricated for a run that never
    measured the subject.
    """
    engine = make_engine()
    create_pending_run(engine, "task-harness-error")
    scored: list[str] = []
    monkeypatch.setattr(
        tasks, "_autoscore_run", lambda _session, run_id: scored.append(run_id)
    )
    monkeypatch.setattr(
        tasks,
        "_run_objective_validation",
        lambda _session, run_id: scored.append(f"validate:{run_id}"),
    )
    executor = FakeExecutor(FakeHandle([("info", "SSE connection reset")], exit_code=2))

    _execute_run_body("task-harness-error", executor, db_engine=engine)

    state, exit_code, failure = get_run_state(engine, "task-harness-error")
    assert state == RunState.failed
    assert exit_code == 2
    assert failure is not None
    assert scored == []


# --------------------------------------------------------------------------
# Whose silence is it — the per-harness watchdog vocabulary
# --------------------------------------------------------------------------


def test_a_quiet_gateway_in_a_claude_run_is_not_the_agent_stalling(
    monkeypatch: pytest.MonkeyPatch,
):
    """The four talos runs, and why 122 minutes were charged to the model.

    The watchdog's markers were opencode's literals applied to every run, so a
    `--harness claude` run could never look like it was waiting on a model. Its
    every silence became `Run stalled:` — kind `stalled`, which the leaderboard
    counts as an attempt the agent failed. The claude adapter now prints
    `# awaiting model` the moment a tool result goes back to the model, and the
    verdict follows the evidence.
    """
    engine = make_engine()
    create_pending_run(engine, "task-claude-quiet", harness="claude")

    class FakeSettings(RunSettings):
        run_timeout_seconds = 3600
        run_stall_seconds = 1  # a tool call would be killed in one second
        run_model_wait_seconds = 2  # the model gets two
        run_max_stream_errors = 0

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())

    program = (
        "import time\nprint('# awaiting model (Bash)', flush=True)\ntime.sleep(30)\n"
    )

    _execute_run_body("task-claude-quiet", _script_executor(program), db_engine=engine)

    _state, _exit_code, failure = get_run_state(engine, "task-claude-quiet")
    assert failure is not None
    assert failure.startswith("No model response:"), failure
    assert is_unmeasured(failure) is True


def test_a_harness_the_lab_has_never_seen_does_not_get_its_silence_blamed(
    monkeypatch: pytest.MonkeyPatch,
):
    """Adding a CLI must not silently start inventing agent stalls.

    A harness whose log vocabulary nothing here knows produces silences nobody
    can attribute, and an unattributable silence is unmeasured — never a result
    about the agent.
    """
    engine = make_engine()
    create_pending_run(engine, "task-unknown-quiet", harness="some-new-cli")

    class FakeSettings(RunSettings):
        run_timeout_seconds = 3600
        run_stall_seconds = 1
        run_model_wait_seconds = 2
        run_max_stream_errors = 0

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())

    program = "import time\nprint('doing something', flush=True)\ntime.sleep(30)\n"

    _execute_run_body("task-unknown-quiet", _script_executor(program), db_engine=engine)

    _state, _exit_code, failure = get_run_state(engine, "task-unknown-quiet")
    assert failure is not None
    assert failure.startswith("No model response:"), failure
    assert is_unmeasured(failure) is True


def test_the_worker_stops_waiting_on_a_process_that_will_not_end():
    """`handle.wait()` alone is unbounded, so a wedged handle parked the worker.

    The watchdog decides *why* a run must end; this is what guarantees the
    worker gets to act on that decision and write the verdict.
    """

    class WedgedHandle:
        def __init__(self) -> None:
            self.stopped = False

        def iter_lines(self):
            yield from ()

        def stop(self) -> None:
            self.stopped = True

        def wait(self) -> int:
            time.sleep(30)
            return 0

    handle = WedgedHandle()
    started = time.monotonic()
    exit_code = tasks._wait_bounded(handle, 1.0, grace_seconds=0.5)
    elapsed = time.monotonic() - started

    assert exit_code == -1, "no exit code was observed, and none may be invented"
    assert handle.stopped is True
    assert elapsed < 10.0, "the worker was still parked on a run that was over"


# --- The shared-tree lease (ADR-0082) -------------------------------------


class MemoryLeases:
    """The lease rules of `RedisTreeLeases`, in memory and without expiry."""

    def __init__(self) -> None:
        self.writer: str | None = None
        self.readers: set[str] = set()
        self.waiting: str | None = None
        self.calls: list[tuple[str, str, bool]] = []

    def _wait_in_line(self, run_id: str) -> str | None:
        if self.waiting is None or self.waiting == run_id:
            self.waiting = run_id
            return None
        return self.waiting

    def try_acquire(self, run_id: str, exclusive: bool) -> str | None:
        self.calls.append(("acquire", run_id, exclusive))
        if exclusive and self.writer == run_id:
            return None
        if self.writer and self.writer != run_id:
            if exclusive:
                self._wait_in_line(run_id)
            return self.writer
        if exclusive:
            ahead = self._wait_in_line(run_id)
            if ahead:
                return ahead
            if self.readers:
                return sorted(self.readers)[0]
            self.writer = run_id
            self.waiting = None
            return None
        if self.waiting and self.waiting != run_id and run_id not in self.readers:
            return self.waiting
        self.readers.add(run_id)
        return None

    def refresh(self, run_id: str, exclusive: bool) -> bool:
        return self.writer == run_id if exclusive else run_id in self.readers

    def release(self, run_id: str, exclusive: bool) -> None:
        self.calls.append(("release", run_id, exclusive))
        if exclusive:
            if self.writer == run_id:
                self.writer = None
            if self.waiting == run_id:
                self.waiting = None
        else:
            self.readers.discard(run_id)


def test_a_plain_run_takes_the_tree_shared_and_gives_it_back():
    engine = make_engine()
    create_pending_run(engine, "lease-plain", command=["true"])
    leases = MemoryLeases()

    from harness_lab_app.tasks import _execute_run_body

    _execute_run_body(
        "lease-plain",
        FakeExecutor(FakeHandle([], 0)),
        db_engine=engine,
        lane="runs-parallel",
        leases=leases,
    )

    assert leases.calls[0] == ("acquire", "lease-plain", False)
    assert leases.calls[-1] == ("release", "lease-plain", False)
    assert leases.readers == set()
    with Session(engine) as session:
        assert RunService(session).get_run("lease-plain").lane == "runs-parallel"


def test_an_ablation_waits_for_the_other_lane_and_stays_pending(monkeypatch):
    """The other lane holds the tree: the ablating run waits, still `pending`."""
    from harness_lab_app import tasks

    engine = make_engine()
    create_pending_run(engine, "lease-ablation", command=["true"])
    leases = MemoryLeases()
    leases.try_acquire("model-run-on-the-other-lane", exclusive=False)
    monkeypatch.setattr(tasks, "rewrites_shared_tree", lambda _config: True)
    monkeypatch.setattr(tasks, "SHARED_TREE_POLL_SECONDS", 0)
    polls: list[RunState] = []

    def sleep(_seconds: float) -> None:
        polls.append(get_run_state(engine, "lease-ablation")[0])
        # The other lane's run ends after two polls.
        if len(polls) == 2:
            leases.release("model-run-on-the-other-lane", exclusive=False)

    monkeypatch.setattr(tasks.time, "sleep", sleep)

    tasks._execute_run_body(
        "lease-ablation",
        FakeExecutor(FakeHandle([], 0)),
        db_engine=engine,
        leases=leases,
    )

    assert polls == [RunState.pending, RunState.pending]
    assert get_run_state(engine, "lease-ablation")[0] != RunState.pending
    assert ("release", "lease-ablation", True) in leases.calls
    with Session(engine) as session:
        messages = [e.message for e in session.exec(select(RunEvent)).all()]
    assert any("waits for the lab's shared tree" in m for m in messages)


def test_a_run_cancelled_while_it_waits_never_starts(monkeypatch):
    from harness_lab_app import tasks

    engine = make_engine()
    create_pending_run(engine, "lease-cancelled", command=["true"])
    leases = MemoryLeases()
    leases.try_acquire("an-ablation", exclusive=True)
    monkeypatch.setattr(tasks, "SHARED_TREE_POLL_SECONDS", 0)

    def sleep(_seconds: float) -> None:
        with Session(engine) as session:
            RunService(session).cancel_run("lease-cancelled")

    monkeypatch.setattr(tasks.time, "sleep", sleep)
    executor = FakeExecutor(FakeHandle([], 0))

    tasks._execute_run_body(
        "lease-cancelled", executor, db_engine=engine, leases=leases
    )

    assert get_run_state(engine, "lease-cancelled")[0] == RunState.cancelled
    assert executor.started_with is None


def test_a_run_cancelled_just_after_it_gets_the_lease_never_starts():
    """The lease is free, but a cancel lands before the run is marked running.

    `mark_running` used to overwrite whatever state the row had, so the
    cancelled run came back to life and its container started.
    """
    engine = make_engine()
    create_pending_run(engine, "lease-cancel-race", command=["true"])

    class CancelOnAcquire(MemoryLeases):
        def try_acquire(self, run_id: str, exclusive: bool) -> str | None:
            holder = super().try_acquire(run_id, exclusive)
            with Session(engine) as session:
                RunService(session).cancel_run(run_id)
            return holder

    leases = CancelOnAcquire()
    executor = FakeExecutor(FakeHandle([], 0))

    _execute_run_body("lease-cancel-race", executor, db_engine=engine, leases=leases)

    assert get_run_state(engine, "lease-cancel-race")[0] == RunState.cancelled
    assert executor.started_with is None
    # The lease it took is given back.
    assert leases.readers == set()


def test_a_broken_lease_store_holds_the_run_pending(monkeypatch):
    """No lease store, no shared tree: the run waits rather than risk the tree.

    Running anyway would let an ablation rewrite the skill catalog under a model
    comparison on the other lane, which corrupts both measurements.
    """
    from harness_lab_app import tasks

    engine = make_engine()
    create_pending_run(engine, "lease-broken", command=["true"])
    monkeypatch.setattr(tasks, "SHARED_TREE_POLL_SECONDS", 0)
    outages = iter([True, True, False])

    class Flaky(MemoryLeases):
        def try_acquire(self, run_id: str, exclusive: bool) -> str | None:
            if next(outages):
                raise ConnectionError("redis down")
            return super().try_acquire(run_id, exclusive)

    polls: list[RunState] = []
    monkeypatch.setattr(
        tasks.time,
        "sleep",
        lambda _s: polls.append(get_run_state(engine, "lease-broken")[0]),
    )
    executor = FakeExecutor(FakeHandle([], 0))

    tasks._execute_run_body("lease-broken", executor, db_engine=engine, leases=Flaky())

    # It waited out the outage `pending`, then ran once the store answered.
    assert polls == [RunState.pending, RunState.pending]
    assert executor.started_with is not None
    with Session(engine) as session:
        messages = [e.message for e in session.exec(select(RunEvent)).all()]
    assert sum("lease store unavailable" in m for m in messages) == 1


def test_a_run_cancelled_while_the_lease_store_is_down_never_starts(monkeypatch):
    from harness_lab_app import tasks

    engine = make_engine()
    create_pending_run(engine, "lease-down-cancel", command=["true"])
    monkeypatch.setattr(tasks, "SHARED_TREE_POLL_SECONDS", 0)

    class Broken(MemoryLeases):
        def try_acquire(self, run_id: str, exclusive: bool) -> str | None:
            raise ConnectionError("redis down")

    def sleep(_seconds: float) -> None:
        with Session(engine) as session:
            RunService(session).cancel_run("lease-down-cancel")

    monkeypatch.setattr(tasks.time, "sleep", sleep)
    executor = FakeExecutor(FakeHandle([], 0))

    tasks._execute_run_body(
        "lease-down-cancel", executor, db_engine=engine, leases=Broken()
    )

    assert get_run_state(engine, "lease-down-cancel")[0] == RunState.cancelled
    assert executor.started_with is None


def test_the_lease_is_refreshed_while_the_run_lasts(monkeypatch):
    from harness_lab_app import tasks

    leases = MemoryLeases()
    leases.try_acquire("long-run", exclusive=False)
    refreshed = threading.Event()

    def refresh(run_id: str, exclusive: bool) -> bool:
        refreshed.set()
        return True

    monkeypatch.setattr(leases, "refresh", refresh)
    monkeypatch.setattr(tasks, "REFRESH_SECONDS", 0.01)
    stop = threading.Event()
    lost = threading.Event()
    keeper = threading.Thread(
        target=tasks._keep_lease, args=(leases, "long-run", False, stop, lost)
    )
    keeper.start()
    assert refreshed.wait(2)
    stop.set()
    keeper.join(2)
    assert not keeper.is_alive()
    assert not lost.is_set()


@pytest.mark.parametrize(
    "refresh_result",
    [False, ConnectionError("redis down")],
    ids=["lease-expired", "store-unreachable"],
)
def test_a_lease_that_cannot_be_refreshed_is_reported_lost(monkeypatch, refresh_result):
    """A refresh that fails or finds no lease means the run is unprotected."""
    from harness_lab_app import tasks

    def refresh(run_id: str, exclusive: bool) -> bool:
        if isinstance(refresh_result, Exception):
            raise refresh_result
        return refresh_result

    leases = MemoryLeases()
    monkeypatch.setattr(leases, "refresh", refresh)
    monkeypatch.setattr(tasks, "REFRESH_SECONDS", 0.01)
    # One bad refresh is not yet a lost lease: it outlives two missed ones.
    monkeypatch.setattr(tasks, "LEASE_SECONDS", 0.05)
    stop = threading.Event()
    lost = threading.Event()
    keeper = threading.Thread(
        target=tasks._keep_lease, args=(leases, "long-run", False, stop, lost)
    )
    keeper.start()
    assert lost.wait(2)
    keeper.join(2)
    assert not keeper.is_alive(), "a lost lease is not refreshed any further"


def test_a_lease_unreachable_for_one_refresh_is_not_lost(monkeypatch):
    """Redis blinking once must not kill an hour-long run."""
    from harness_lab_app import tasks

    answers = iter([ConnectionError("blip"), True, True, True, True, True])
    calls: list[int] = []

    def refresh(run_id: str, exclusive: bool) -> bool:
        calls.append(1)
        answer = next(answers, True)
        if isinstance(answer, Exception):
            raise answer
        return answer

    leases = MemoryLeases()
    monkeypatch.setattr(leases, "refresh", refresh)
    monkeypatch.setattr(tasks, "REFRESH_SECONDS", 0.01)
    monkeypatch.setattr(tasks, "LEASE_SECONDS", 10)
    stop = threading.Event()
    lost = threading.Event()
    keeper = threading.Thread(
        target=tasks._keep_lease, args=(leases, "long-run", False, stop, lost)
    )
    keeper.start()
    deadline = time.monotonic() + 2
    while len(calls) < 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    keeper.join(2)
    assert len(calls) >= 4
    assert not lost.is_set()


def test_a_run_whose_lease_is_lost_is_stopped_as_a_lab_failure(monkeypatch):
    """Past its lease the run no longer owns the tree; it must not go on."""
    from harness_lab_app import tasks

    monkeypatch.setattr(tasks, "get_settings", lambda: RunSettings())
    monkeypatch.setattr(tasks, "REFRESH_SECONDS", 0.01)
    monkeypatch.setattr(tasks, "LEASE_SECONDS", 0.02)
    engine = make_engine()
    create_pending_run(engine, "lease-lost", command=["true"])

    class Expiring(MemoryLeases):
        def refresh(self, run_id: str, exclusive: bool) -> bool:
            return False

    class Hanging(SubprocessRunExecutor):
        def start(self, run_id: str, command: list[str], **kwargs):
            return super().start(
                run_id,
                [
                    "python3",
                    "-c",
                    "import time\nwhile True:\n print('working', flush=True)\n"
                    " time.sleep(0.05)",
                ],
            )

    started = time.monotonic()
    _execute_run_body("lease-lost", Hanging(), db_engine=engine, leases=Expiring())

    state, _, failure = get_run_state(engine, "lease-lost")
    assert time.monotonic() - started < 30
    assert state == RunState.failed
    assert failure is not None and failure.startswith("Shared-tree lease lost:")
    # The lab lost its own lock; nothing about the model was measured.
    assert is_unmeasured(failure)


def test_a_lease_is_given_up_before_redis_could_expire_it(monkeypatch):
    """Redis drops the lease LEASE_SECONDS after the last refresh it saw.

    Waiting a full lease period to notice left a window where another run could
    already hold the tree. One refresh period of margin closes it, and still
    tolerates a single missed refresh.
    """
    from harness_lab_app import tasks

    clock = {"now": 0.0}
    monkeypatch.setattr(tasks.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tasks, "REFRESH_SECONDS", 30)
    monkeypatch.setattr(tasks, "LEASE_SECONDS", 120)
    failures: list[float] = []

    class Down(MemoryLeases):
        def refresh(self, run_id: str, exclusive: bool) -> bool:
            failures.append(clock["now"])
            raise ConnectionError("redis down")

    class Ticks:
        """`stop.wait` that advances the fake clock one refresh period."""

        def wait(self, seconds: float) -> bool:
            clock["now"] += seconds
            return clock["now"] > 1000

    lost = threading.Event()
    tasks._keep_lease(Down(), "long-run", False, Ticks(), lost)  # type: ignore[arg-type]

    assert lost.is_set()
    assert failures[-1] < 120
    assert len(failures) >= 2, "one missed refresh alone must not lose it"


def test_a_cancelled_waiting_run_gives_up_its_waiting_place(monkeypatch):
    """Or new shared runs stay blocked behind a run that will never start."""
    from harness_lab_app import tasks

    engine = make_engine()
    create_pending_run(engine, "lease-cancel-waiting", command=["true"])
    leases = MemoryLeases()
    leases.try_acquire("model-1", exclusive=False)
    monkeypatch.setattr(tasks, "rewrites_shared_tree", lambda _config: True)
    monkeypatch.setattr(tasks, "SHARED_TREE_POLL_SECONDS", 0)

    def sleep(_seconds: float) -> None:
        with Session(engine) as session:
            RunService(session).cancel_run("lease-cancel-waiting")

    monkeypatch.setattr(tasks.time, "sleep", sleep)

    tasks._execute_run_body(
        "lease-cancel-waiting",
        FakeExecutor(FakeHandle([], 0)),
        db_engine=engine,
        leases=leases,
    )

    assert leases.waiting is None
    assert leases.try_acquire("model-2", exclusive=False) is None


def test_a_worker_with_no_lease_store_does_not_run_unprotected(monkeypatch):
    """A Redis URL that cannot even be parsed is an outage, not a free pass."""
    from harness_lab_app import tasks

    def broken(_url: str) -> Any:
        raise ValueError("bad redis url")

    monkeypatch.setattr(tasks, "redis_leases", broken)
    monkeypatch.setattr(tasks, "init_db", lambda: None)
    monkeypatch.setattr(tasks, "get_executor", lambda: "executor")
    seen: dict[str, Any] = {}

    def body(run_id: str, executor: Any, **kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(tasks, "_execute_run_body", body)
    tasks.execute_run("lease-no-store")

    assert seen["leases"] is not None
    with pytest.raises(ConnectionError):
        seen["leases"].try_acquire("lease-no-store", False)
