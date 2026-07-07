from __future__ import annotations

import json
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

    def start(self, run_id: str, command: list[str]) -> FakeHandle:
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
            config=RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc"),
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
