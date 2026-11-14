"""A phased case is paced one phase per step, and the agent cannot read its grade.

ADR-0079. The first frontier run of chess3d-v1 built the whole game in one session
and declared itself done; its agent also read the lab's memory notes about the
case. These tests hold the runner to pacing and the agent's container to isolation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from harness_lab_app import executors, tasks
from harness_lab_app.case_profile import CaseProfileError, agent_hidden_for
from harness_lab_app.case_service import (
    CaseService,
    contract_section,
    phase_prompts_from_task,
)
from harness_lab_app.executors import (
    GOVERNANCE_MASK_ALL,
    GOVERNANCE_MASK_NONE,
    RUNNER_GRADING_DIRS,
    runner_masks,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bench_run  # noqa: E402

CHESS = REPO_ROOT / "benchmarks" / "chess3d-v1" / "task.yaml"


def make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


# --- Phases are seeded from task.yaml --------------------------------------------


def test_a_phased_case_runs_one_step_per_phase() -> None:
    with Session(make_engine()) as session:
        case = CaseService(session).import_task_yaml(CHESS)
    raw = yaml.safe_load(CHESS.read_text())
    assert len(case.step_prompts) == len(raw["phases"]) == 4
    assert case.max_steps == 1 + len(raw["phases"])


def test_every_phase_carries_the_contract_and_only_its_own_instruction() -> None:
    """Each step is a fresh session: a phase that omitted the contract would be
    built against an API the agent never read."""
    with Session(make_engine()) as session:
        case = CaseService(session).import_task_yaml(CHESS)
    for number, phase in enumerate(case.step_prompts, start=2):
        assert phase.startswith(f"## Phase {number} of 5")
        assert "## The API contract (exact)" in phase
        assert "## The scene contract (exact)" in phase
        assert "Phase 1 of 5" not in phase


def test_the_first_phase_is_the_case_prompt_and_says_to_stop_there() -> None:
    text = (REPO_ROOT / "benchmarks/chess3d-v1/prompt_default.md").read_text()
    assert "## Phase 1 of 5" in text
    assert "Stop when phase 1 is done and verified" in text
    assert contract_section(text).startswith("## What is being judged")


def test_a_phase_file_outside_the_case_is_refused(tmp_path: Path) -> None:
    case_dir = tmp_path / "benchmarks" / "x"
    case_dir.mkdir(parents=True)
    task = case_dir / "task.yaml"
    task.write_text(
        yaml.safe_dump(
            {"task_id": "x", "phases": ["benchmarks/other/phase.md"], "prompts": {}}
        )
    )
    with pytest.raises(ValueError, match="inside x/"):
        phase_prompts_from_task(yaml.safe_load(task.read_text()), task)


def test_reseeding_refreshes_the_phases_and_the_step_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(make_engine()) as session:
        service = CaseService(session)
        case = service.import_task_yaml(CHESS)
        case.step_prompts = ["stale"]
        case.max_steps = 10
        session.add(case)
        session.commit()
        monkeypatch.setattr(
            "harness_lab_app.case_service.get_settings",
            lambda: SimpleNamespace(repo_root=REPO_ROOT),
        )
        service.seed_benchmark_cases()
        session.refresh(case)
        assert case.max_steps == 5
        assert case.step_prompts[0].startswith("## Phase 2 of 5")


def test_a_case_that_drops_its_phases_goes_back_to_its_target_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clearing `phases` must not leave the 1 + N budget of phases that are gone."""
    case_dir = tmp_path / "benchmarks" / "paced"
    (case_dir / "phases").mkdir(parents=True)
    (case_dir / "prompt_default.md").write_text(
        "## What is being judged\n\nx\n\n## How the work is paced\n\nPhase 1.\n"
    )
    (case_dir / "phases" / "p2.md").write_text("## Phase 2 of 2\n")
    task = case_dir / "task.yaml"
    raw = {
        "task_id": "paced",
        "target_level": 7,
        "prompts": {"default": "benchmarks/paced/prompt_default.md"},
        "phases": ["benchmarks/paced/phases/p2.md"],
    }
    task.write_text(yaml.safe_dump(raw))
    settings = SimpleNamespace(repo_root=tmp_path)
    monkeypatch.setattr("harness_lab_app.case_service.get_settings", lambda: settings)
    with Session(make_engine()) as session:
        service = CaseService(session)
        case = service.import_task_yaml(task)
        assert case.max_steps == 2
        del raw["phases"]
        task.write_text(yaml.safe_dump(raw))
        service.seed_benchmark_cases()
        session.refresh(case)
        assert case.step_prompts == []
        assert case.max_steps == 7


def test_the_harness_variant_carries_its_preamble_into_every_phase() -> None:
    """Otherwise governance would apply to phase 1 alone."""
    with Session(make_engine()) as session:
        case = CaseService(session).import_task_yaml(CHESS)
    preamble = (REPO_ROOT / "benchmarks/_harness_preamble.md").read_text()
    harness = tasks._phase_prompts_for_variant(case, "harness")
    default = tasks._phase_prompts_for_variant(case, "default")
    assert all(phase.startswith(preamble) for phase in harness)
    assert not any(preamble in phase for phase in default)


# --- The runner paces the phases --------------------------------------------------


def _phased_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phases: list[str]):
    """Drive bench_run.main with an agent that edits a file and claims victory."""
    monkeypatch.setattr(bench_run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bench_run, "DATA_DIR", tmp_path / "data" / "runs")
    monkeypatch.setattr(bench_run, "WORKSPACE_ROOT", tmp_path / "workspace")
    for name in (
        "_load_env",
        "apply_gateway_overlay",
        "restore_full_skill_views",
        "write_bench_context",
        "apply_skill_views",
        "pull_usage_litellm",
        "pull_usage_langfuse",
        "pull_usage_opencode_db",
    ):
        monkeypatch.setattr(bench_run, name, lambda *_a, **_k: None)
    monkeypatch.setattr(bench_run, "build_effective_skills", lambda *_a, **_k: [])
    monkeypatch.setattr(bench_run, "copy_to_clipboard", lambda *_a, **_k: False)
    monkeypatch.setattr(bench_run.shutil, "which", lambda name: f"/usr/bin/{name}")
    prompts: list[str] = []

    def fake_run(cmd: list[str], log_path: Path, **_kwargs: Any) -> int:
        # The prompt is the last argument the harness command carries.
        prompts.append(cmd[-1])
        workspace = tmp_path / "workspace" / "r-1"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / f"step{len(prompts)}.gd").write_text(str(len(prompts)))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("done.\nALL MILESTONES COMPLETE\n")
        return 0

    monkeypatch.setattr(bench_run, "run_opencode", fake_run)
    out_dir = tmp_path / "data" / "runs" / "r-1"
    out_dir.mkdir(parents=True)
    (out_dir / "case_spec.json").write_text(
        json.dumps(
            {
                "case_id": "phased",
                "prompt": "Phase one.",
                "prompt_variant": "default",
                "repo": "",
                "step_prompts": phases,
                "max_steps": 1 + len(phases),
            }
        )
    )
    code = bench_run.main(
        [
            "--case",
            "phased",
            "--run",
            "r-1",
            "--harness",
            "claude",
            "--executor-model",
            "stub",
            "--score-mode",
            "manual",
            "--max-steps",
            str(1 + len(phases)),
            "--fresh",
        ]
    )
    return code, out_dir, prompts


def test_each_phase_drives_its_own_step_in_order(tmp_path, monkeypatch) -> None:
    """`step_prompts[0]` drives step 2; it used to be skipped (ADR-0079)."""
    code, _out_dir, prompts = _phased_run(tmp_path, monkeypatch, ["PHASE-B", "PHASE-C"])
    assert code == 0
    assert len(prompts) == 3
    assert "Phase one." in prompts[0]
    assert "PHASE-B" in prompts[1] and "PHASE-C" not in prompts[1]
    assert "PHASE-C" in prompts[2]


def test_the_agent_cannot_end_a_phased_run_before_the_last_phase(
    tmp_path, monkeypatch
) -> None:
    """The fake agent says ALL MILESTONES COMPLETE in every step; the run goes on."""
    _code, out_dir, prompts = _phased_run(tmp_path, monkeypatch, ["B", "C"])
    assert len(prompts) == 3
    usage = json.loads((out_dir / "usage.json").read_text())
    # After the last phase the signal ends the run as it always has.
    assert usage["stop_reason"] == "completion_signal"


def test_a_middle_phase_tells_the_agent_to_stop_there(tmp_path) -> None:
    path = bench_run.generate_case_step_prompt(
        "r-1", 2, tmp_path, "Do B.", phases_left=2
    )
    text = path.read_text()
    assert "Do not start the next phase" in text
    assert "ALL MILESTONES COMPLETE" not in text
    last = bench_run.generate_case_step_prompt("r-1", 4, tmp_path, "Do D.")
    assert "ALL MILESTONES COMPLETE" in last.read_text()


# --- The prompt names the case's own entry point ----------------------------------


def test_the_prompt_names_the_case_entry_point_not_main_py() -> None:
    assert bench_run._entry_point({"entry_point": "project.godot"}) == "project.godot"
    assert bench_run._entry_point({}) == "main.py"
    for bad in ("/etc/passwd", "../x", "a\nb"):
        assert bench_run._entry_point({"entry_point": bad}) == "main.py"


def test_the_web_and_godot_cases_declare_their_entry_point() -> None:
    expected = {
        "chess3d-v1": "project.godot",
        "todo-web-v1": "index.html",
        "frontend-bugfix-v1": "index.html",
    }
    for case, entry in expected.items():
        raw = yaml.safe_load(
            (REPO_ROOT / "benchmarks" / case / "task.yaml").read_text()
        )
        assert raw["entry_point"] == entry


# --- The agent's container cannot read its grade ----------------------------------


def test_the_lab_references_are_masked_for_every_agent_condition() -> None:
    for level in (GOVERNANCE_MASK_NONE, GOVERNANCE_MASK_ALL):
        tmpfs, _files = runner_masks(level, hidden=[])
        for directory in RUNNER_GRADING_DIRS:
            assert f"/app/{directory}" in tmpfs


def test_a_case_grader_rubric_and_notes_are_masked_for_its_agent() -> None:
    raw = yaml.safe_load(CHESS.read_text())
    hidden = agent_hidden_for(raw)
    tmpfs, files = runner_masks(GOVERNANCE_MASK_NONE, hidden=hidden)
    assert "/app/benchmarks/chess3d-v1/grader" in tmpfs
    masked_files = {target for target, _kind in files}
    assert "/app/benchmarks/chess3d-v1/scoring.md" in masked_files
    assert "/app/benchmarks/chess3d-v1/README.md" in masked_files
    # The prompt the agent works from is never masked.
    assert "/app/benchmarks/chess3d-v1/prompt_default.md" not in masked_files


def test_the_validation_lane_still_reads_the_grader() -> None:
    """`hidden=None` is every container but the agent's."""
    tmpfs, files = runner_masks(GOVERNANCE_MASK_NONE)
    assert not any("reference" in path or "chess3d" in path for path in tmpfs)
    assert not any("chess3d" in target for target, _kind in files)


def test_a_hidden_path_outside_the_case_is_refused() -> None:
    for bad in ("benchmarks/other/grader/", "../etc", "/app/data", "memory/"):
        with pytest.raises(CaseProfileError):
            agent_hidden_for({"task_id": "chess3d-v1", "agent_hidden": [bad]})


def test_the_agent_run_masks_the_grade_and_validation_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the worker: the agent's start carries `hidden`."""
    from harness_lab_app.models import Case, CaseStatus, CaseType
    from harness_lab_app.run_service import RunService
    from harness_lab_app.schemas import RunConfigCreate, RunCreate

    engine = make_engine()
    with Session(engine) as session:
        session.add(
            Case(
                case_id="chess3d-v1",
                name="chess3d-v1",
                type=CaseType.build_from_scratch,
                status=CaseStatus.active,
                metadata_json={"agent_hidden": ["benchmarks/chess3d-v1/grader/"]},
            )
        )
        session.commit()
        RunService(session).create_run(
            RunCreate(
                run_id="iso-1",
                config=RunConfigCreate(
                    case_id="chess3d-v1", prompt_variant="default", executor_model="m"
                ),
            )
        )

    class Settings:
        run_timeout_seconds = 3600
        run_stall_seconds = 3600
        run_model_wait_seconds = 3600
        run_max_stream_errors = 0
        data_runs_dir = tmp_path / "runs"

    monkeypatch.setattr(tasks, "get_settings", lambda: Settings())
    seen: dict[str, Any] = {}

    class Executor:
        def start(self, run_id: str, command: list[str], **kwargs: Any):
            seen.update(kwargs)
            return SimpleNamespace(
                iter_lines=lambda: iter(()), wait=lambda: 0, stop=lambda: None
            )

    tasks._execute_run_body("iso-1", Executor(), db_engine=engine)
    assert seen["hidden"] == ["benchmarks/chess3d-v1/grader/"]


def test_the_host_lane_accepts_a_grading_mask_without_failing_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subprocess has nothing to mount over; a grading mask changes no condition."""
    monkeypatch.setattr(
        executors, "get_settings", lambda: SimpleNamespace(repo_root=REPO_ROOT)
    )
    handle = executors.SubprocessRunExecutor().start("r", ["true"], hidden=[])
    assert handle.wait() == 0


def test_the_host_lane_refuses_a_case_image_instead_of_ignoring_it() -> None:
    """A Godot case run on the host would call an engine it may not have."""
    with pytest.raises(RuntimeError, match="needs the docker backend"):
        executors.SubprocessRunExecutor().start(
            "r", ["true"], image="harness-lab-runner-godot:latest"
        )
    # No image means the shared one, which the host lane stands in for.
    assert executors.SubprocessRunExecutor().start("r", ["true"], image="").wait() == 0


# --- Every 3D game case follows the lane's rules -----------------------------------


def _game_cases() -> list[Path]:
    return [
        task
        for task in sorted((REPO_ROOT / "benchmarks").glob("*/task.yaml"))
        if yaml.safe_load(task.read_text()).get("category") == "game-3d"
    ]


def test_every_3d_game_case_is_phased_hidden_and_named() -> None:
    """What chess3d-v1 K-001 taught applies to the whole lane (ADR-0079): a
    frontier game is paced in phases, its agent cannot read its grader, and the
    prompt names the real entry point."""
    cases = _game_cases()
    assert len(cases) >= 1
    for task in cases:
        raw = yaml.safe_load(task.read_text())
        case = task.parent.name
        assert len(raw.get("phases") or []) >= 3, case
        hidden = agent_hidden_for(raw)
        assert f"benchmarks/{case}/grader/" in hidden, case
        assert f"benchmarks/{case}/scoring.md" in hidden, case
        assert raw.get("entry_point") == "project.godot", case
        prompt = (REPO_ROOT / raw["prompts"]["default"]).read_text()
        assert contract_section(prompt), f"{case}: no contract block for its phases"
        commands = raw["validation_commands"]
        assert f"benchmarks/{case}/grader" in commands[0], f"{case}: no tamper guard"
        assert " harvest " in commands[1], f"{case}: no harvest step"


def test_every_3d_game_case_seeds_with_one_step_per_phase() -> None:
    for task in _game_cases():
        raw = yaml.safe_load(task.read_text())
        with Session(make_engine()) as session:
            case = CaseService(session).import_task_yaml(task)
        assert case.max_steps == 1 + len(raw["phases"]), task.parent.name
        assert all("## The API contract (exact)" in p for p in case.step_prompts)
