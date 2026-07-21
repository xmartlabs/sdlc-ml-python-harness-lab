"""Staged-skill lifecycle: CRUD keeps DB+disk in sync; promote writes to the
governed external catalog and re-syncs, but never commits."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import staged_skill_service as module
from harness_lab_app.run_service import RunPreflightError, RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate
from harness_lab_app.staged_skill_service import StagedSkillError, StagedSkillService

CONTENT = "---\nname: probe\ndescription: probes things\n---\nDo the probe.\n"


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


@pytest.fixture
def service_env(tmp_path, monkeypatch):
    """Point the service at a temp repo and neutralize governed-name lookups."""
    monkeypatch.setattr(
        module, "get_settings", lambda: SimpleNamespace(repo_root=tmp_path)
    )
    monkeypatch.setattr(module, "known_skill_names", lambda: {"brainstorming"})
    return tmp_path


def test_create_writes_db_and_disk(service_env):
    with _session() as session:
        skill = StagedSkillService(session).create_skill("probe", "probes", CONTENT)
        assert skill.status == "draft"
        staged = service_env / "data" / "skills_staging" / "probe" / "SKILL.md"
        assert staged.read_text() == CONTENT


def test_create_validates_name_collision_and_content(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        with pytest.raises(StagedSkillError, match="kebab"):
            service.create_skill("Bad Name!", "", CONTENT)
        with pytest.raises(StagedSkillError, match="collides"):
            service.create_skill("brainstorming", "", CONTENT)
        with pytest.raises(StagedSkillError, match="empty"):
            service.create_skill("probe", "", "   ")
        service.create_skill("probe", "", CONTENT)
        with pytest.raises(StagedSkillError, match="already exists"):
            service.create_skill("probe", "", CONTENT)


def test_update_rewrites_disk_and_validates_status(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.create_skill("probe", "", CONTENT)
        updated = service.update_skill(
            "probe", content=CONTENT + "More.\n", status="testing"
        )
        assert updated.status == "testing"
        staged = service_env / "data" / "skills_staging" / "probe" / "SKILL.md"
        assert staged.read_text().endswith("More.\n")
        with pytest.raises(StagedSkillError, match="Invalid status"):
            service.update_skill("probe", status="shipped")


def test_delete_removes_row_and_dir(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.create_skill("probe", "", CONTENT)
        service.delete_skill("probe")
        assert not (service_env / "data" / "skills_staging" / "probe").exists()
        with pytest.raises(StagedSkillError, match="not found"):
            service.get_skill("probe")


def test_active_names_excludes_archived(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.create_skill("probe", "", CONTENT)
        service.create_skill("shelved", "", CONTENT)
        service.update_skill("shelved", status="archived")
        assert service.active_names() == {"probe"}


def test_promote_writes_external_syncs_and_never_commits(service_env, monkeypatch):
    synced = []
    with _session() as session:
        service = StagedSkillService(session)
        service.create_skill("probe", "", CONTENT)
        monkeypatch.setattr(
            StagedSkillService, "_sync_skill_views", lambda _self: synced.append(True)
        )
        result = service.promote_skill("probe")
        dest = service_env / ".github" / "skills-external" / "probe" / "SKILL.md"
        assert dest.read_text() == CONTENT
        assert synced == [True]
        assert "commit" in result["next_steps"]
        assert service.get_skill("probe").status == "promoted"


def test_preflight_rejects_unknown_staged_extra():
    with (
        _session() as session,
        pytest.raises(RunPreflightError, match="skills_extra"),
    ):
        RunService(session).create_run(
            RunCreate(
                run_id="extra-1",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                    skills_extra=["ghost-skill"],
                ),
            )
        )


def test_preflight_accepts_active_staged_extra(service_env):
    with _session() as session:
        StagedSkillService(session).create_skill("probe", "", CONTENT)
        run = RunService(session).create_run(
            RunCreate(
                run_id="extra-2",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                    skills_extra=["probe"],
                ),
            )
        )
        assert "--extra-skill" in run.command
