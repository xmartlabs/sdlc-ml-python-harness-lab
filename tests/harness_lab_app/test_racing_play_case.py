"""racing-play-v1: the case definition is wired to the grader and the tool."""

from __future__ import annotations

import importlib.util
import re
import stat
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE = REPO_ROOT / "benchmarks" / "racing-play-v1"
TASK = CASE / "task.yaml"
RAW = yaml.safe_load(TASK.read_text())
SCRIPTED_SENTENCE = (
    "You may also write a program that calls `race step` for you; that is allowed, "
    "and the run will be labelled as scripted rather than played."
)


def _grader():
    spec = importlib.util.spec_from_file_location(
        "racing_grade_case", CASE / "grader" / "grade.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_task_id_matches_directory_and_starter_exists() -> None:
    assert RAW["task_id"] == CASE.name
    assert (REPO_ROOT / RAW["starter"]).is_dir()
    shim = CASE / "starter" / "race"
    assert shim.stat().st_mode & stat.S_IXUSR


def test_validation_commands_match_grader_checks() -> None:
    commands = RAW["validation_commands"]
    assert commands[0].startswith("git -C /app diff --quiet HEAD -- ")
    for guarded in ("grader", "starter"):
        assert f"benchmarks/racing-play-v1/{guarded}" in commands[0]
    assert "frontend/src/gameplay/racing.mjs" in commands[0]
    names = [re.search(r"grade\.py check (\w+) ", c).group(1) for c in commands[1:]]
    assert tuple(names) == _grader().CHECKS
    for command in commands[1:]:
        assert '"$PWD"' in command and "{run_id}" in command


def test_grader_is_hidden_from_agent() -> None:
    from harness_lab_app.case_profile import agent_hidden_for

    hidden = agent_hidden_for(RAW)
    assert "benchmarks/racing-play-v1/grader/" in hidden
    assert "benchmarks/racing-play-v1/scoring.md" in hidden


def test_case_is_a_one_step_report() -> None:
    from harness_lab_app.case_profile import profile_from_task
    from harness_lab_app.case_service import step_budget_from_task

    assert step_budget_from_task(RAW, []) == 1
    assert profile_from_task(RAW)["deliverable"] == "report"


def test_prompt_states_scripting_is_allowed() -> None:
    for variant in ("default", "harness"):
        text = (REPO_ROOT / RAW["prompts"][variant]).read_text()
        assert SCRIPTED_SENTENCE in text, variant


def test_prompt_does_not_reveal_the_ladder_or_par() -> None:
    text = (CASE / "prompt_default.md").read_text()
    for secret in ("PAR_SECONDS", "progress_25", "grade.py", "20 s", "par"):
        assert secret not in text, secret


def test_case_imports_through_the_case_service() -> None:
    from sqlmodel import Session, SQLModel, create_engine
    from sqlmodel.pool import StaticPool

    from harness_lab_app.case_service import CaseService

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        case = CaseService(session).import_task_yaml(TASK)
    assert case.case_id == "racing-play-v1"
    assert case.metadata_json["starter_dir"] == "benchmarks/racing-play-v1/starter"
    assert len(case.validation_commands) == 9
    assert set(case.prompt_variants) == {"default", "harness"}
