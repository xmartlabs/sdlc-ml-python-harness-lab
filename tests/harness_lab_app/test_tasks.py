from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from harness_lab_app import tasks
from harness_lab_app.executors import SubprocessRunExecutor
from harness_lab_app.models import RunState
from harness_lab_app.run_failures import is_unmeasured
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate
from harness_lab_app.tasks import _execute_run_body, reconcile_stale_runs


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


def create_pending_run(engine, run_id: str, command: list[str] | None = None):
    with Session(engine) as session:
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

    class FakeSettings(RunSettings):
        run_timeout_seconds = 0
        run_stall_seconds = 0

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
        def start(self, run_id: str, command: list[str]):
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
        def start(self, run_id: str, command: list[str]):
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
        def start(self, run_id: str, command: list[str]):
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
