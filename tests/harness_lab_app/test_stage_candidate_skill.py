"""`make stage-candidate`: a case's candidate skill reaches staging intact (ADR-0087)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import staged_skill_service as service_module
from harness_lab_app.skill_import import build_skill
from harness_lab_app.staged_skill_service import StagedSkillService

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "stage_candidate_skill", REPO_ROOT / "scripts" / "stage_candidate_skill.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


script = _load_script()


def test_every_candidate_in_the_corpus_is_found_and_hidden_by_its_case():
    import yaml

    found = script.candidates()
    assert "review_pull_request" in found
    for name, folder in found.items():
        case = folder.parent.parent
        hidden = yaml.safe_load((case / "task.yaml").read_text()).get(
            "agent_hidden", []
        )
        assert f"benchmarks/{case.name}/candidate-skill/" in hidden, name


def test_every_candidate_is_a_valid_skill_named_after_its_folder():
    for name, folder in script.candidates().items():
        imported = build_skill(script.read_tree(folder), fallback_name=name)
        assert imported.name == name
        assert imported.description, name


def test_a_candidate_declared_twice_is_refused(tmp_path):
    for case in ("a", "b"):
        skill = tmp_path / "benchmarks" / case / "candidate-skill" / "dup"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: dup\ndescription: d\n---\n")
    with pytest.raises(SystemExit, match="declared twice"):
        script.candidates(tmp_path)


def test_a_candidate_stages_as_a_whole_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(
        service_module, "get_settings", lambda: SimpleNamespace(repo_root=tmp_path)
    )
    monkeypatch.setattr(service_module, "known_skill_names", lambda: set())
    folder = script.candidates()["review_pull_request"]
    imported = build_skill(
        script.read_tree(folder),
        fallback_name="review_pull_request",
        source_kind=script.SOURCE_KIND,
    )
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        service = StagedSkillService(session)
        skill = service.import_skill(imported, name="review_pull_request")
        assert skill.name in service.active_names()
        staged = (
            tmp_path / "data" / "skills_staging" / "review_pull_request" / "SKILL.md"
        )
        assert staged.read_bytes() == (folder / "SKILL.md").read_bytes()
