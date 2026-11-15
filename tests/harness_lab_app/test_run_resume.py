"""Retrying a stopped run from scratch, or from where it stopped (ADR-0083)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from harness_lab_app import run_service as run_service_module
from harness_lab_app.models import RunEvent, RunState, Score
from harness_lab_app.run_resume import restore_step, resume_point, wipe_for_scratch
from harness_lab_app.run_service import RunRetryError, RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate


def _step(run_dir: Path, n: int, *, kind: str = "snapshot", files: dict | None = None):
    steps = run_dir / "steps"
    steps.mkdir(parents=True, exist_ok=True)
    meta = {"step": n, "kind": kind, "seconds": 60.0 * n, "prompt_kind": "case_step"}
    if kind == "snapshot":
        snap = steps / f"step-{n}.snapshot"
        snap.mkdir()
        for name, text in (files or {"main.gd": f"# after step {n}\n"}).items():
            (snap / name).write_text(text)
        meta["path"] = snap.name
    (steps / f"step-{n}.meta.json").write_text(json.dumps(meta))
    (steps / f"step-{n}.log").write_text(f"log {n}\n")
    (steps / f"step-{n}.events.jsonl").write_text("{}\n")


# --- where a run can continue ----------------------------------------------


def test_a_run_continues_after_its_last_completed_step(tmp_path):
    run_dir = tmp_path / "run"
    for n in (1, 2, 3):
        _step(run_dir, n)
    # Step 4 started and was cut: its events exist, its meta does not.
    (run_dir / "steps" / "step-4.events.jsonl").write_text("{}\n")

    point = resume_point(run_dir, max_steps=5)

    assert point.available and point.last_step == 3


def test_a_run_that_never_finished_a_step_has_nothing_to_keep(tmp_path):
    assert not resume_point(tmp_path / "run", max_steps=5).available
    _step(tmp_path / "run", 2)
    point = resume_point(tmp_path / "run", max_steps=5)
    assert not point.available
    assert "Step 1 never finished" in point.reason


def test_a_cloned_repository_step_cannot_be_restored(tmp_path):
    run_dir = tmp_path / "run"
    _step(run_dir, 1, kind="diff")
    point = resume_point(run_dir, max_steps=5)
    assert not point.available
    assert "diff" in point.reason and "from scratch" in point.reason


def test_a_run_that_used_every_step_has_nothing_left_to_continue(tmp_path):
    run_dir = tmp_path / "run"
    for n in (1, 2):
        _step(run_dir, n)
    point = resume_point(run_dir, max_steps=2)
    assert not point.available
    assert "no step left" in point.reason


def test_restoring_puts_back_the_step_and_drops_the_half_done_one(tmp_path):
    run_dir = tmp_path / "run"
    workspace = tmp_path / "workspace"
    for n in (1, 2, 3):
        _step(run_dir, n)
    (run_dir / "steps" / "step-4.events.jsonl").write_text("{}\n")
    (run_dir / "steps" / "step-4-prompt.md").write_text("phase 4\n")
    workspace.mkdir()
    (workspace / "main.gd").write_text("# half of step 4\n")
    (workspace / "broken.gd").write_text("# left by the cut step\n")

    restore_step(run_dir, workspace, 3)

    assert (workspace / "main.gd").read_text() == "# after step 3\n"
    assert not (workspace / "broken.gd").exists()
    names = {p.name for p in (run_dir / "steps").iterdir()}
    assert "step-3.meta.json" in names and "step-3.snapshot" in names
    assert not any(name.startswith("step-4") for name in names)


def test_a_scratch_retry_keeps_only_the_run_configuration(tmp_path):
    run_dir = tmp_path / "run"
    workspace = tmp_path / "workspace"
    _step(run_dir, 1)
    for name in ("case_spec.json", "usage.json", "metadata.json"):
        (run_dir / name).write_text("{}")
    (run_dir / "masks").mkdir()
    (run_dir / "workspace").mkdir()
    workspace.mkdir()
    (workspace / "main.gd").write_text("x")

    wipe_for_scratch(run_dir, workspace)

    assert not workspace.exists()
    assert {p.name for p in run_dir.iterdir()} == {"case_spec.json", "masks"}


# --- the service -------------------------------------------------------------


@pytest.fixture
def lab(monkeypatch, tmp_path):
    monkeypatch.setattr(
        run_service_module,
        "get_settings",
        lambda: SimpleNamespace(
            data_runs_dir=tmp_path / "data" / "runs",
            workspace_dir=tmp_path / "workspace",
            repo_root=tmp_path,
        ),
    )
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    published: list[str] = []
    with Session(engine) as session:
        service = RunService(session, enqueue=published.append)
        service.create_run(
            RunCreate(
                run_id="chess3d-v1-K-108",
                config=RunConfigCreate(
                    case_id="chess3d-v1",
                    prompt_variant="harness",
                    harness="claude",
                    executor_model="claude-opus-5-5",
                    max_steps=5,
                    dry_run=True,
                ),
            )
        )
        service.mark_running("chess3d-v1-K-108", lane="runs")
        service.mark_finished("chess3d-v1-K-108", -1, "Worker restarted")
        published.clear()
        yield SimpleNamespace(
            service=service,
            session=session,
            published=published,
            run_dir=tmp_path / "data" / "runs" / "chess3d-v1-K-108",
            workspace=tmp_path / "workspace" / "chess3d-v1-K-108",
        )


def test_continue_restarts_at_the_step_after_the_last_completed_one(lab):
    for n in (1, 2, 3):
        _step(lab.run_dir, n)
    lab.workspace.mkdir(parents=True)
    (lab.workspace / "main.gd").write_text("# half of step 4\n")

    options = lab.service.retry_options("chess3d-v1-K-108")
    assert options["continue_from_step"] == 4
    run = lab.service.retry_run("chess3d-v1-K-108", "continue")

    assert run.state == RunState.pending
    assert lab.published == ["chess3d-v1-K-108"]
    command = run.command
    assert "--resume" in command
    assert command[command.index("--from-step") + 1] == "4"
    assert (lab.workspace / "main.gd").read_text() == "# after step 3\n"
    events = lab.session.exec(
        select(RunEvent).where(RunEvent.run_id == "chess3d-v1-K-108")
    ).all()
    assert any("continued from step 4" in e.message for e in events)


def test_continue_is_refused_when_nothing_can_be_kept(lab):
    with pytest.raises(RunRetryError, match="nothing to keep"):
        lab.service.retry_run("chess3d-v1-K-108", "continue")
    assert lab.published == []
    options = lab.service.retry_options("chess3d-v1-K-108")
    assert options["continue_from_step"] is None
    assert options["continue_reason"]


def test_scratch_discards_the_attempt_and_runs_every_step(lab):
    for n in (1, 2):
        _step(lab.run_dir, n)
    lab.workspace.mkdir(parents=True)
    lab.session.add(Score(run_id="chess3d-v1-K-108", name="checks", value_num=0.5))
    lab.session.commit()

    run = lab.service.retry_run("chess3d-v1-K-108", "scratch")

    assert "--resume" not in run.command and "--from-step" not in run.command
    assert not lab.workspace.exists()
    assert not (lab.run_dir / "steps").exists()
    scores = lab.session.exec(
        select(Score).where(Score.run_id == "chess3d-v1-K-108")
    ).all()
    assert scores == []
    assert lab.published == ["chess3d-v1-K-108"]


def test_a_scratch_retry_after_a_continue_starts_at_step_one(lab):
    for n in (1, 2):
        _step(lab.run_dir, n)
    lab.service.retry_run("chess3d-v1-K-108", "continue")
    lab.service.mark_running("chess3d-v1-K-108")
    lab.service.mark_finished("chess3d-v1-K-108", -1, "cut again")

    run = lab.service.retry_run("chess3d-v1-K-108", "scratch")

    assert "--from-step" not in run.command
    config = lab.service.get_run_config(run.run_config_id)
    assert config.resume_from_step == 0 and config.resume is False


def test_only_a_stopped_run_is_retried_by_the_new_modes(lab):
    lab.service.retry_run("chess3d-v1-K-108", "scratch")  # now pending
    with pytest.raises(RunRetryError, match="only a failed or cancelled"):
        lab.service.retry_run("chess3d-v1-K-108", "scratch")


def test_an_unknown_mode_is_refused(lab):
    with pytest.raises(RunRetryError, match="Unknown retry mode"):
        lab.service.retry_run("chess3d-v1-K-108", "rewind")
