"""`scripts/import_case.py` refreshes a case's generated fields on request.

ADR-0025 makes the DB the source of truth, so a re-import leaves an existing
case alone by default. That is right for operator-owned fields and wrong for
the prompt list, which is generated from the YAML: after the ADR-0047 migration
an already-imported case kept pointing at variant names the files no longer
carry, and a run against one of them ships a prompt that is a filename.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from harness_lab_app.models import Case

# scripts/ is not a package; make import_case importable the same way it runs.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import import_case  # noqa: E402

TASK_YAML = """
task_id: probe-case-v1
version: "1"
status: active
category: agentic-build
description: A probe case.
subject_repo: ml-python-base
target_level: 6
complexity: low
validation_commands:
  - "test 1 = 1"
prompts:
  default: benchmarks/probe/prompt_default.md
  harness: benchmarks/probe/prompt_harness.md
"""


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(import_case, "get_engine", lambda: engine)
    monkeypatch.setattr(import_case, "init_db", lambda: None)
    return engine


def _seed_stale(engine) -> None:
    with Session(engine) as session:
        session.add(
            Case(
                case_id="probe-case-v1",
                name="Operator's own name",
                description="edited in the browser",
                type="build_from_scratch",
                prompt_variants={"naive": "benchmarks/probe/prompt_naive.md"},
                validation_commands=["test 0 = 1"],
            )
        )
        session.commit()


def _write_task(tmp_path: Path) -> Path:
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK_YAML)
    return task_path


def test_reimport_without_refresh_leaves_the_case_untouched(engine, tmp_path, capsys):
    _seed_stale(engine)
    assert import_case.main(["--task", str(_write_task(tmp_path))]) == 0
    assert "--refresh to update" in capsys.readouterr().out
    with Session(engine) as session:
        case = session.exec(select(Case).where(Case.case_id == "probe-case-v1")).one()
        assert list(case.prompt_variants) == ["naive"]


def test_refresh_rewrites_prompts_and_keeps_operator_fields(engine, tmp_path):
    _seed_stale(engine)
    assert import_case.main(["--task", str(_write_task(tmp_path)), "--refresh"]) == 0
    with Session(engine) as session:
        case = session.exec(select(Case).where(Case.case_id == "probe-case-v1")).one()
        assert sorted(case.prompt_variants) == ["default", "harness"]
        assert case.validation_commands == ["test 1 = 1"]
        # Refresh syncs what the YAML generates, not what an operator authored.
        assert case.name == "Operator's own name"
        assert case.description == "edited in the browser"
