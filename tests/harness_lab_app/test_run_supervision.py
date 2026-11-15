"""Watching a long run: a real clock, visible progress, a decision when a phase is slow.

ADR-0080. racing3d-v1-K-001 ran 5 h 13 min against a 2-hour budget because the
watchdog's clock stopped while the host slept; phase 4 legitimately took 52
minutes where phases 1-3 took 5-14; phase 5 sat 43 minutes in a silent test
with nothing on screen saying so; and the cut threw away four phases of work.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from harness_lab_app import tasks
from harness_lab_app.case_profile import (
    CaseProfileError,
    profile_from_task,
    slow_policy_for,
)
from harness_lab_app.executors import SubprocessRunExecutor
from harness_lab_app.models import Case, CaseStatus, CaseType, RunState
from harness_lab_app.phase_watch import (
    LivePhaseGrader,
    PhaseWatch,
    expected_seconds,
    phase_history,
)
from harness_lab_app.run_failures import classify_failure, failure_label
from harness_lab_app.run_progress import (
    LIVE_STEPS_FILE,
    SLOW_STATE_FILE,
    phase_title,
    read_progress,
)
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate


def make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _event(kind: str, at: str, **extra: Any) -> str:
    return json.dumps({"_received_at": at, "type": kind, **extra}) + "\n"


def _assistant(at: str, command: str, tokens: int = 10) -> str:
    return _event(
        "assistant",
        at,
        message={
            "usage": {"output_tokens": tokens},
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": command}}
            ],
        },
    )


def _write_run(run_dir: Path) -> None:
    """Two finished phases and a third in flight, as bench_run leaves them."""
    steps = run_dir / "steps"
    steps.mkdir(parents=True)
    (steps / "step-1.events.jsonl").write_text(
        _assistant("2026-11-14T10:00:00Z", "godot --version")
        + _event("result", "2026-11-14T10:10:00Z", total_cost_usd=2.0, num_turns=12)
    )
    (steps / "step-1.meta.json").write_text(json.dumps({"step": 1, "seconds": 600.0}))
    (steps / "step-1.snapshot").mkdir()
    (steps / "step-2-prompt.md").write_text(
        "## Phase 2 of 3 — the race rules\n\nDo it."
    )
    (steps / "step-2.events.jsonl").write_text(
        _assistant("2026-11-14T10:10:05Z", "edit race.gd")
        + _event("result", "2026-11-14T10:15:00Z", total_cost_usd=1.5, num_turns=9)
    )
    (steps / "step-2.meta.json").write_text(json.dumps({"step": 2, "seconds": 295.0}))
    (steps / "step-2.snapshot").mkdir()
    (steps / "step-3-prompt.md").write_text("## Phase 3 of 3 — AI rivals\n")
    (steps / "step-3.events.jsonl").write_text(
        _assistant("2026-11-14T10:15:10Z", "write ai.gd", tokens=40)
        + _assistant("2026-11-14T10:20:00Z", "timeout 1500 godot-render --script t.gd")
    )


# --- A. The budget is measured on a clock that keeps running ------------------------


def test_the_watchdog_measures_wall_time_not_a_clock_that_stops_in_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sleeping host freezes the VM's monotonic clock; the wall clock keeps going."""
    clock = iter([1000.0, 1000.0, 1000.0, 99999.0, 99999.0, 99999.0, 99999.0])
    monkeypatch.setattr(tasks, "_wall_clock", lambda: next(clock, 99999.0))
    monkeypatch.setattr(tasks, "_run_controls", lambda *_a: (False, 0))

    class Quiet:
        stopped = False

        def iter_lines(self) -> Iterator[tuple[str, str]]:
            import time

            while not self.stopped:
                time.sleep(0.05)
            return
            yield  # pragma: no cover

        def stop(self) -> None:
            self.stopped = True

        def wait(self) -> int:
            return 0

    outcome = tasks._stream_run(
        SimpleNamespace(add=lambda *_: None, commit=lambda: None),  # type: ignore[arg-type]
        "r",
        Quiet(),
        timeout_seconds=3600,
    )
    assert outcome.stop_reason == "timeout"


# --- B. What a live run is doing, read from its files -------------------------------


def test_progress_reads_phases_time_cost_and_the_last_action(tmp_path: Path) -> None:
    _write_run(tmp_path)
    from datetime import datetime

    # An hour after the last event (10:20 UTC).
    now = datetime.fromisoformat("2026-11-14T11:20:00+00:00").timestamp()
    progress = read_progress(
        tmp_path,
        max_steps=3,
        first_prompt="## Phase 1 of 3 — the car and the track (do only this now)\n",
        now=now,
    )
    steps = progress["steps"]
    assert [s["name"] for s in steps] == [
        "the car and the track",
        "the race rules",
        "AI rivals",
    ]
    assert [s["done"] for s in steps] == [True, True, False]
    assert steps[0]["seconds"] == 600.0 and steps[0]["cost_usd"] == 2.0
    assert steps[2]["cost_usd"] is None and steps[2]["turns"] == 2
    assert progress["current_step"] == 3
    assert progress["phase_total"] == 3
    assert progress["cost_usd"] == 3.5 and progress["cost_complete"] is False
    assert "godot-render" in progress["last_action"]
    assert progress["quiet_seconds"] == 3600.0


def test_progress_shows_each_phase_grade_and_the_slow_flag(tmp_path: Path) -> None:
    _write_run(tmp_path)
    (tmp_path / LIVE_STEPS_FILE).write_text(
        json.dumps(
            {"steps": {"1": {"passed": 12, "total": 39}, "2": {"unmeasured": True}}}
        )
    )
    (tmp_path / SLOW_STATE_FILE).write_text(json.dumps({"step": 3, "on_slow": "stop"}))
    progress = read_progress(tmp_path, max_steps=3)
    assert progress["steps"][0]["grade"] == {"passed": 12, "total": 39}
    assert progress["steps"][1]["grade"] == {"unmeasured": True}
    assert progress["slow"]["step"] == 3


def test_a_phase_heading_drops_its_instruction() -> None:
    assert phase_title("## Phase 1 of 5 — the rules core (do only this now)\n") == (
        1,
        5,
        "the rules core",
    )
    assert phase_title("no heading") is None


# --- C. A slow phase asks a person before it is stopped ------------------------------


def test_the_expected_time_comes_from_the_case_then_history_then_the_floor() -> None:
    history = {4: [3000.0, 3200.0, 2800.0], 2: [400.0]}
    assert (
        expected_seconds(4, case_budget_minutes=20, history=history, floor_seconds=60)
        == 1200
    )
    assert (
        expected_seconds(4, case_budget_minutes=None, history=history, floor_seconds=60)
        == 9000
    )
    # One sample is not a history: the floor applies.
    assert (
        expected_seconds(
            2, case_budget_minutes=None, history=history, floor_seconds=2700
        )
        == 2700
    )
    # A fast phase is never given less than the floor.
    fast = {1: [100.0, 120.0]}
    assert (
        expected_seconds(1, case_budget_minutes=None, history=fast, floor_seconds=2700)
        == 2700
    )


def test_history_reads_only_finished_steps_of_the_same_case(tmp_path: Path) -> None:
    for run, step, secs in (
        ("c-K-001", 1, 600),
        ("c-K-002", 1, 700),
        ("other-K-001", 1, 9),
    ):
        steps = tmp_path / run / "steps"
        steps.mkdir(parents=True, exist_ok=True)
        (steps / f"step-{step}.meta.json").write_text(
            json.dumps({"step": step, "seconds": secs})
        )
    history = phase_history(tmp_path, "c", "c-K-003")
    # The other case's 9-second step never enters this case's history.
    assert sorted(history[1]) == [600.0, 700.0]


def _watch(tmp_path: Path, on_slow: str = "stop") -> tuple[PhaseWatch, list[str]]:
    flags: list[str] = []
    watch = PhaseWatch(
        run_dir=tmp_path,
        budget_for=lambda _step: 600,
        grace_seconds=300,
        on_slow=on_slow,
        on_flag=flags.append,
    )
    return watch, flags


def test_a_slow_phase_is_flagged_then_stopped_when_nobody_answers(
    tmp_path: Path,
) -> None:
    watch, flags = _watch(tmp_path)
    assert watch.tick(4, 0.0, 0, now=500.0) is None
    assert not (tmp_path / SLOW_STATE_FILE).exists()
    # Past its expected time: flagged once, not stopped.
    assert watch.tick(4, 0.0, 0, now=700.0) is None
    assert len(flags) == 1 and "Phase 4 is slower than expected" in flags[0]
    state = json.loads((tmp_path / SLOW_STATE_FILE).read_text())
    assert state["step"] == 4 and state["decide_by"] == 1000.0
    assert watch.tick(4, 0.0, 0, now=900.0) is None
    assert len(flags) == 1
    # Grace period over, nobody answered, the policy is stop.
    assert watch.tick(4, 0.0, 0, now=1001.0) == "slow_phase"


def test_keep_waiting_lifts_the_flag_until_the_new_deadline(tmp_path: Path) -> None:
    watch, flags = _watch(tmp_path)
    watch.tick(4, 0.0, 0, now=700.0)
    assert (tmp_path / SLOW_STATE_FILE).exists()
    # A person granted 30 minutes: the phase is no longer late.
    assert watch.tick(4, 0.0, 1800, now=1100.0) is None
    assert not (tmp_path / SLOW_STATE_FILE).exists()
    # Late again past the new deadline: flagged again, with a fresh grace.
    assert watch.tick(4, 0.0, 1800, now=2500.0) is None
    assert len(flags) == 2


def test_a_wait_policy_flags_but_never_stops(tmp_path: Path) -> None:
    watch, flags = _watch(tmp_path, on_slow="wait")
    watch.tick(4, 0.0, 0, now=700.0)
    assert watch.tick(4, 0.0, 0, now=99_999.0) is None
    assert flags and "keep running" in flags[0]


def test_a_new_phase_starts_its_own_clock(tmp_path: Path) -> None:
    watch, flags = _watch(tmp_path)
    watch.tick(4, 0.0, 0, now=700.0)
    assert flags
    assert watch.tick(5, 690.0, 0, now=1001.0) is None


def test_time_granted_to_one_phase_is_not_carried_into_the_next(
    tmp_path: Path,
) -> None:
    """`extra_wait_seconds` is the run's running total; each phase gets only its own."""
    watch, flags = _watch(tmp_path)
    # Phase 1 runs late and a person grants it 30 minutes.
    watch.tick(1, 0.0, 0, now=700.0)
    assert watch.tick(1, 0.0, 1800, now=1100.0) is None
    # Phase 2 starts with the run's total still at 1800: none of it is phase 2's,
    # so it is flagged at its own 600-second budget, not at 2400.
    assert watch.tick(2, 2000.0, 1800, now=2500.0) is None
    assert not flags[1:]
    assert watch.tick(2, 2000.0, 1800, now=2601.0) is None
    assert len(flags) == 2 and "Phase 2 is slower than expected" in flags[1]
    # Time granted now, while phase 2 runs, is phase 2's.
    assert watch.tick(2, 2000.0, 3600, now=2700.0) is None
    assert not (tmp_path / SLOW_STATE_FILE).exists()


def test_the_final_validation_waits_out_the_longest_live_grade() -> None:
    """A Godot harvest may take 900 s: a 300 s wait let validation race it."""
    from harness_lab_app.validation import spec_deadline_seconds

    spec = {
        "setup": [["sh", "-c", "install"]],
        "commands": [
            ["bash", "-c", "cd w && timeout -k 5 900 python grade.py harvest"],
            ["bash", "-c", "cd w && timeout -k 5 120 python grade.py check a"],
        ],
    }
    # Each command at its own deadline (budget + kill grace + margin), plus the
    # setup at the default backstop.
    assert spec_deadline_seconds(spec) == 900 + (900 + 35) + (120 + 35)


def test_join_reports_whether_the_live_grade_finished(tmp_path: Path) -> None:
    import threading

    _write_run(tmp_path)
    release = threading.Event()

    def grade(_step: int, _first: bool) -> dict[str, Any]:
        release.wait(5)
        return {"passed": 1, "total": 1}

    grader = LivePhaseGrader(tmp_path, grade)
    assert grader.join(0.01) is True  # nothing in flight
    grader.poll()
    assert grader.join(0.05) is False  # still grading
    release.set()
    assert grader.join(5) is True


def test_the_case_declares_its_phase_budget_and_slow_policy() -> None:
    profile = profile_from_task({"phase_budget_minutes": 30, "on_slow": "wait"})
    assert profile["phase_budget_minutes"] == 30 and profile["on_slow"] == "wait"
    assert slow_policy_for(SimpleNamespace(metadata_json=profile)) == (30, "wait")
    assert slow_policy_for(SimpleNamespace(metadata_json={})) == (None, "stop")
    for bad in (
        {"phase_budget_minutes": 0},
        {"phase_budget_minutes": "x"},
        {"on_slow": "maybe"},
    ):
        with pytest.raises(CaseProfileError):
            profile_from_task(bad)


def test_keep_waiting_adds_time_only_to_a_running_run() -> None:
    engine = make_engine()
    with Session(engine) as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="kw-1",
                config=RunConfigCreate(
                    case_id="c", prompt_variant="default", executor_model="m"
                ),
            )
        )
        assert (
            service.keep_waiting("kw-1", 1800).extra_wait_seconds == 0
        )  # still pending
        service.mark_running("kw-1")
        service.keep_waiting("kw-1", 1800)
        assert service.keep_waiting("kw-1", 1800).extra_wait_seconds == 3600


def test_the_slow_verdict_is_a_result_about_the_agent() -> None:
    reason = (
        "Phase too slow: it ran past its expected time, nobody chose to keep waiting"
    )
    assert classify_failure(reason) == "timeout"
    assert failure_label(reason) == "phase too slow"


def test_the_live_grader_grades_each_finished_phase_once(tmp_path: Path) -> None:
    _write_run(tmp_path)
    calls: list[tuple[int, bool]] = []

    def grade(step: int, first: bool) -> dict[str, Any]:
        calls.append((step, first))
        return {"passed": step, "total": 39}

    grader = LivePhaseGrader(tmp_path, grade)
    grader.poll()
    grader.join(5)
    grader.poll()
    grader.join(5)
    grader.poll()
    grader.join(5)
    # Newest first, dependencies installed on the first grade only.
    assert calls == [(2, True), (1, False)]
    stored = json.loads((tmp_path / LIVE_STEPS_FILE).read_text())["steps"]
    assert stored == {"2": {"passed": 2, "total": 39}, "1": {"passed": 1, "total": 39}}


def test_a_failing_live_grade_is_recorded_and_never_raises(tmp_path: Path) -> None:
    _write_run(tmp_path)

    def grade(_step: int, _first: bool) -> dict[str, Any]:
        raise RuntimeError("docker down")

    grader = LivePhaseGrader(tmp_path, grade)
    grader.poll()
    grader.join(5)
    stored = json.loads((tmp_path / LIVE_STEPS_FILE).read_text())["steps"]
    assert "docker down" in stored["2"]["error"]


# --- D. A cut run is graded on what it built -----------------------------------------


class RunSettings:
    run_timeout_seconds = 3600
    run_stall_seconds = 3600
    run_model_wait_seconds = 3600
    run_max_stream_errors = 0
    run_phase_floor_seconds = 0
    run_slow_grace_seconds = 900


def _pending(engine, run_id: str, max_steps: int = 1) -> None:
    with Session(engine) as session:
        session.add(
            Case(
                case_id="cut-case",
                name="cut-case",
                type=CaseType.build_from_scratch,
                status=CaseStatus.active,
                validation_commands=["cd {workspace} && test -f main.gd"],
            )
        )
        session.commit()
        RunService(session).create_run(
            RunCreate(
                run_id=run_id,
                config=RunConfigCreate(
                    case_id="cut-case",
                    prompt_variant="default",
                    executor_model="m",
                    max_steps=max_steps,
                ),
            )
        )


@pytest.mark.parametrize(
    ("verdict", "graded"),
    [
        ("stalled", True),
        ("timeout", True),
        ("slow_phase", True),
        ("cancelled", True),
        ("model_silent", False),
        ("endpoint", False),
    ],
)
def test_which_cuts_grade_the_work_left(verdict: str, graded: bool) -> None:
    assert tasks._cut_measures_the_agent(verdict) is graded


def test_a_stalled_run_is_archived_and_graded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine()
    _pending(engine, "cut-1")
    live = tmp_path / "workspace" / "cut-1"
    live.mkdir(parents=True)
    (live / "main.gd").write_text("extends Node\n")

    class Settings(RunSettings):
        run_stall_seconds = 1
        data_runs_dir = tmp_path / "data" / "runs"
        workspace_dir = tmp_path / "workspace"

    monkeypatch.setattr(tasks, "get_settings", lambda: Settings())
    graded: list[str] = []
    monkeypatch.setattr(
        tasks, "_run_objective_validation", lambda _s, rid: graded.append(rid)
    )
    monkeypatch.setattr(
        tasks, "_run_step_validation", lambda _s, rid: graded.append(rid)
    )

    class Silent(SubprocessRunExecutor):
        def start(self, run_id: str, command: list[str], **kwargs: Any):
            return super().start(
                run_id,
                [
                    "python3",
                    "-c",
                    "print('working', flush=True); import time; time.sleep(30)",
                ],
            )

    tasks._execute_run_body("cut-1", Silent(), db_engine=engine)
    with Session(engine) as session:
        run = RunService(session).get_run("cut-1")
        events = [
            e.message for e in RunService(session).list_events("cut-1", ascending=True)
        ]
    assert run.state == RunState.failed and "stalled" in (run.failure_reason or "")
    assert (tmp_path / "data" / "runs" / "cut-1" / "workspace" / "main.gd").is_file()
    assert graded == ["cut-1", "cut-1"]
    assert any("grading the work it left" in message for message in events)


def test_a_cut_with_nothing_built_is_not_graded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Settings(RunSettings):
        data_runs_dir = tmp_path / "data" / "runs"
        workspace_dir = tmp_path / "workspace"

    monkeypatch.setattr(tasks, "get_settings", lambda: Settings())
    assert tasks._archive_cut_workspace("nothing-1") is False


def test_a_paced_case_sets_its_own_ceiling_and_every_game_case_declares_one() -> None:
    from pathlib import Path as _Path

    import yaml

    from harness_lab_app.case_profile import run_timeout_for

    assert (
        run_timeout_for(
            SimpleNamespace(metadata_json={"run_timeout_minutes": 360}), 7200
        )
        == 21600
    )
    assert run_timeout_for(SimpleNamespace(metadata_json={}), 7200) == 7200
    with pytest.raises(CaseProfileError):
        profile_from_task({"run_timeout_minutes": 5})
    root = _Path(__file__).resolve().parents[2] / "benchmarks"
    for task in sorted(root.glob("*/task.yaml")):
        raw = yaml.safe_load(task.read_text())
        if raw.get("category") == "game-3d":
            profile = profile_from_task(raw)
            assert profile["phase_budget_minutes"] > 0, task
            assert profile["run_timeout_minutes"] * 60 > 7200, task


def test_a_cut_retry_replaces_the_archive_its_first_attempt_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """racing3d-v1-K-001: the retry built five phases on the live workspace while
    the archive still held the two files of the attempt the network killed."""

    class Settings(RunSettings):
        data_runs_dir = tmp_path / "data" / "runs"
        workspace_dir = tmp_path / "workspace"

    monkeypatch.setattr(tasks, "get_settings", lambda: Settings())
    stale = tmp_path / "data" / "runs" / "r-1" / "workspace"
    stale.mkdir(parents=True)
    (stale / "project.godot").write_text("first attempt")
    live = tmp_path / "workspace" / "r-1"
    (live / "scripts").mkdir(parents=True)
    (live / "project.godot").write_text("retry")
    (live / "scripts" / "race.gd").write_text("extends Node")

    assert tasks._archive_cut_workspace("r-1") is True
    assert (stale / "project.godot").read_text() == "retry"
    assert (stale / "scripts" / "race.gd").is_file()
    assert not (stale.parent / "workspace.cut-tmp").exists()
