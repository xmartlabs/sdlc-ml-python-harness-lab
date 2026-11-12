from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from harness_lab_app import validation_service as vs_module
from harness_lab_app.models import Case, CaseStatus, CaseType
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate
from harness_lab_app.validation_service import ValidationService


class FakeHandle:
    def __init__(self, lines: list[str], exit_code: int):
        self._lines = lines
        self._exit_code = exit_code

    def iter_lines(self) -> Iterator[tuple[str, str]]:
        for line in self._lines:
            yield ("info", line)

    def stop(self) -> None:  # pragma: no cover - not used here
        pass

    def wait(self) -> int:
        return self._exit_code


class FakeExecutor:
    def __init__(self, exit_code: int, lines: list[str] | None = None):
        self.exit_code = exit_code
        self.lines = lines or []
        self.calls: list[tuple[str, list[str]]] = []

    def start(self, run_id: str, command: list[str], **kwargs) -> FakeHandle:
        self.calls.append((run_id, command))
        return FakeHandle(self.lines, self.exit_code)


def make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def seed_case_and_run(session: Session, run_id: str, run_dir: Path):
    session.add(
        Case(
            case_id="tetris-v1",
            name="tetris-v1",
            type=CaseType.build_from_scratch,
            status=CaseStatus.active,
            validation_commands=[
                "BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy python {workspace}/main.py"
            ],
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
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "usage.json").write_text(
        json.dumps({"input_tokens": 200000, "output_tokens": 2000, "steps": 8})
    )


@pytest.fixture
def patched_settings(tmp_path, monkeypatch):
    runs_dir = tmp_path / "data" / "runs"
    monkeypatch.setattr(
        vs_module, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs_dir)
    )
    return runs_dir


def test_validate_run_records_objective_pass_and_cost(patched_settings):
    engine = make_engine()
    with Session(engine) as session:
        seed_case_and_run(session, "run-ok", patched_settings / "run-ok")
        service = ValidationService(session, executor=FakeExecutor(exit_code=0))

        validation = service.validate_run("run-ok")

        assert validation.objective["passed"] is True
        assert validation.objective["strategy"] == "smoke"
        assert validation.cost["total_tokens"] == 202000
        assert validation.effort["steps"] == 8
        assert validation.composite is None  # no subjective yet


def test_validate_run_records_objective_failure(patched_settings):
    engine = make_engine()
    with Session(engine) as session:
        seed_case_and_run(session, "run-bad", patched_settings / "run-bad")
        executor = FakeExecutor(exit_code=1, lines=["Traceback ..."])
        service = ValidationService(session, executor=executor)

        validation = service.validate_run("run-bad")

        assert validation.objective["passed"] is False
        # The smoke command was actually dispatched with the workspace path.
        assert executor.calls[0][1][-1].endswith("workspace/run-bad/main.py")


def test_apply_subjective_computes_composite(patched_settings):
    engine = make_engine()
    with Session(engine) as session:
        seed_case_and_run(session, "run-score", patched_settings / "run-score")
        service = ValidationService(session, executor=FakeExecutor(exit_code=0))
        service.validate_run("run-score")

        validation = service.apply_subjective(
            "run-score", functional_score=8, interventions=1, notes="playable"
        )

        assert validation.subjective["functional_score"] == 8
        assert validation.composite == 8.0
        assert validation.effort["interventions"] == 1


def test_apply_subjective_capped_when_objective_failed(patched_settings):
    engine = make_engine()
    with Session(engine) as session:
        seed_case_and_run(session, "run-cap", patched_settings / "run-cap")
        service = ValidationService(session, executor=FakeExecutor(exit_code=1))
        service.validate_run("run-cap")

        validation = service.apply_subjective(
            "run-cap", functional_score=9, interventions=0
        )

        assert validation.composite == 1.0


class HangingHandle:
    """A command that produces output and then never ends, until stopped.

    The shape of the fourth run of experiment `...-on--2`: SDL2 turned the
    SIGTERM from `timeout` into an SDL_QUIT event, a second event loop swallowed
    it, and neither `iter_lines()` nor `wait()` had a deadline of its own.
    """

    def __init__(self) -> None:
        self._stopped = threading.Event()
        self.stop_calls = 0

    def iter_lines(self) -> Iterator[tuple[str, str]]:
        yield ("info", "starting")
        self._stopped.wait(timeout=10)

    def stop(self) -> None:
        self.stop_calls += 1
        self._stopped.set()

    def wait(self) -> int:
        # A terminated shell reports its own status; it says nothing about
        # whether the agent's code works.
        return 0


class HangingExecutor:
    def __init__(self) -> None:
        self.handle = HangingHandle()

    def start(self, run_id: str, command: list[str], **kwargs) -> HangingHandle:
        return self.handle


def test_a_wedged_validation_command_is_killed_and_recorded_inconclusive(
    patched_settings, monkeypatch
):
    monkeypatch.setattr(vs_module, "command_deadline_seconds", lambda _label: 0.2)
    executor = HangingExecutor()
    engine = make_engine()
    with Session(engine) as session:
        seed_case_and_run(session, "run-hang", patched_settings / "run-hang")
        validation = ValidationService(session, executor=executor).validate_run(
            "run-hang"
        )

    assert executor.handle.stop_calls == 1, "the watchdog never fired"
    objective = validation.objective
    # Not False: the check settled nothing, so it must not read as "the code
    # does not work" (ADR-0046 — infrastructure is not capability).
    assert objective["passed"] is None
    check = objective["checks"][0]
    assert check["conclusive"] is False
    assert check["exit_code"] == 124
    assert "deadline exceeded" in check["detail"]


def test_a_stored_check_names_the_command_under_the_key_that_says_so():
    """`command` and `name` carry the same string.

    Consumers used to read `command` off a stored check and find None, so an
    audit reported a failing check it could not name.
    """
    from harness_lab_app.validation import parse_objective

    objective = parse_objective(
        [{"command": "sh -c 'pytest -q'", "exit_code": 1, "output": "boom"}],
        strategy="commands",
    )

    check = objective["checks"][0]
    assert check["command"] == "sh -c 'pytest -q'"
    assert check["name"] == check["command"]


class PhaseRecordingExecutor:
    """Records each command's phase; the setup step can be made to fail."""

    def __init__(self, setup_exit: int = 0, check_exit: int = 0, check_lines=None):
        self.setup_exit = setup_exit
        self.check_exit = check_exit
        self.check_lines = check_lines or []
        self.calls: list[tuple[str, str]] = []

    def start(self, run_id: str, command: list[str], **kwargs) -> FakeHandle:
        phase = kwargs.get("phase", "")
        self.calls.append((phase, command[-1]))
        if phase == "setup":
            return FakeHandle(["pip output"], self.setup_exit)
        return FakeHandle(self.check_lines, self.check_exit)


def _seed_case_with_requirements(session: Session, run_id: str, run_dir: Path):
    seed_case_and_run(session, run_id, run_dir)
    case = (
        session.get(Case, 1)
        or session.exec(__import__("sqlmodel").select(Case)).first()
    )
    case.metadata_json = {"validation_requirements": ["pandas==2.2.3"]}
    session.add(case)
    session.commit()


def test_the_setup_step_runs_first_and_with_network(patched_settings):
    engine = make_engine()
    with Session(engine) as session:
        _seed_case_with_requirements(session, "run-deps", patched_settings / "run-deps")
        executor = PhaseRecordingExecutor()
        ValidationService(session, executor=executor).validate_run("run-deps")
    phases = [phase for phase, _ in executor.calls]
    assert phases[0] == "setup"
    assert set(phases[1:]) == {"validate"}


def test_a_failed_setup_makes_the_checks_inconclusive_not_failed(patched_settings):
    """A grader that could not install its own dependencies measured nothing."""
    engine = make_engine()
    with Session(engine) as session:
        _seed_case_with_requirements(
            session, "run-nodeps", patched_settings / "run-nodeps"
        )
        executor = PhaseRecordingExecutor(setup_exit=1, check_exit=1)
        validation = ValidationService(session, executor=executor).validate_run(
            "run-nodeps"
        )
    objective = validation.objective
    assert objective["passed"] is None
    assert all(
        check["inconclusive_reason"] == "missing_dependency"
        for check in objective["checks"]
    )
    assert [phase for phase, _ in executor.calls] == ["setup"]


# --------------------------------------------------------------------------
# Per-step reward: each step's snapshot graded by the same checks (ADR-0076)
# --------------------------------------------------------------------------


class SnapshotAwareExecutor:
    """Passes a check only for the snapshots named in ``passing``."""

    def __init__(self, passing: set[str]):
        self.passing = passing
        self.calls: list[list[str]] = []

    def start(self, run_id: str, command: list[str], **kwargs) -> FakeHandle:
        self.calls.append(command)
        ok = any(name in command[-1] for name in self.passing)
        return FakeHandle([], 0 if ok else 1)


def _seed_steps(run_dir: Path, steps: int, cost: float = 0.25) -> None:
    steps_dir = run_dir / "steps"
    for n in range(1, steps + 1):
        snap = steps_dir / f"step-{n}.snapshot"
        snap.mkdir(parents=True)
        (snap / "main.py").write_text(f"# step {n}\n")
        (steps_dir / f"step-{n}.meta.json").write_text(
            json.dumps({"step": n, "kind": "snapshot", "seconds": 30})
        )
        (steps_dir / f"step-{n}.events.jsonl").write_text(
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": cost,
                    "num_turns": 3,
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                }
            )
            + "\n"
        )


def test_validate_steps_grades_every_snapshot_and_finds_the_resolving_step(
    patched_settings,
):
    engine = make_engine()
    with Session(engine) as session:
        run_dir = patched_settings / "run-steps"
        seed_case_and_run(session, "run-steps", run_dir)
        _seed_steps(run_dir, steps=3)
        executor = SnapshotAwareExecutor(passing={"step-2.snapshot", "step-3.snapshot"})

        trajectory = ValidationService(session, executor=executor).validate_steps(
            "run-steps"
        )

        assert trajectory is not None
        # One check per step, each pointed at that step's snapshot — never at
        # the final workspace.
        assert len(executor.calls) == 3
        assert all("/steps/step-" in call[-1] for call in executor.calls)
        assert not any("workspace/run-steps" in call[-1] for call in executor.calls)
        assert [s["progress"] for s in trajectory["steps"]] == [0.0, 1.0, 1.0]
        assert trajectory["steps_to_resolve"] == 2
        assert trajectory["cost_to_resolve_usd"] == 0.5
        assert trajectory["post_resolution_cost_share"] == pytest.approx(1 / 3)
        stored = ValidationService(session).get_validation("run-steps")
        assert stored is not None
        assert stored.objective["per_step"]["steps_to_resolve"] == 2


def test_validate_steps_is_none_for_a_run_without_step_records(patched_settings):
    engine = make_engine()
    with Session(engine) as session:
        seed_case_and_run(session, "run-old", patched_settings / "run-old")
        service = ValidationService(session, executor=FakeExecutor(exit_code=0))
        assert service.validate_steps("run-old") is None


def test_validate_steps_reads_the_swe_lane_live_verdict(patched_settings):
    engine = make_engine()
    with Session(engine) as session:
        run_dir = patched_settings / "run-swe"
        seed_case_and_run(session, "run-swe", run_dir)
        steps_dir = run_dir / "steps"
        steps_dir.mkdir(parents=True)
        for n, (f2p, resolved) in enumerate([(0, False), (1, True)], start=1):
            (steps_dir / f"step-{n}.meta.json").write_text(
                json.dumps(
                    {
                        "step": n,
                        "kind": "diff",
                        "swe_eval": {
                            "resolved": resolved,
                            "f2p_passed": f2p,
                            "f2p_total": 1,
                            "p2p_passed": 18,
                            "p2p_total": 18,
                        },
                    }
                )
            )
        executor = FakeExecutor(exit_code=0)

        trajectory = ValidationService(session, executor=executor).validate_steps(
            "run-swe"
        )

        assert executor.calls == []  # no re-run: the runner measured it live
        assert trajectory is not None
        assert trajectory["steps_to_resolve"] == 2
        assert trajectory["steps"][0]["progress"] < 1.0
        assert trajectory["sources"] == ["swe_live"]


def test_a_check_that_does_not_name_the_workspace_sits_the_step_pass_out(
    patched_settings,
):
    """A tamper guard on the lab's own git state grades the final state.

    Run against every step it would credit each one with the final verdict and
    inflate the early steps' progress, so it is left out of the per-step pass.
    """
    from sqlmodel import select

    from harness_lab_app.models import Case

    engine = make_engine()
    with Session(engine) as session:
        run_dir = patched_settings / "run-guard"
        seed_case_and_run(session, "run-guard", run_dir)
        case = session.exec(select(Case)).one()
        case.validation_commands = [
            "git -C /app diff --quiet HEAD -- benchmarks/x/tests",
            "BENCH_SMOKE=1 python {workspace}/main.py",
        ]
        session.add(case)
        session.commit()
        _seed_steps(run_dir, steps=2)
        executor = SnapshotAwareExecutor(passing={"step-2.snapshot"})

        trajectory = ValidationService(session, executor=executor).validate_steps(
            "run-guard"
        )

        assert len(executor.calls) == 2  # the smoke per step; never the guard
        assert not any("git -C /app" in call[-1] for call in executor.calls)
        assert [s["total"] for s in trajectory["steps"]] == [1, 1]
        assert trajectory["steps_to_resolve"] == 2
