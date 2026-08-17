"""Multi-file staged skills: the DB is truth, `data/` is rebuildable (ADR-0017).

The invariant these tests lock in: after any operation, the staging tree on disk
equals the file rows in the DB — nothing extra, nothing missing — and wiping the
tree entirely is recoverable with `reconcile_all()`.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import staged_skill_service as module
from harness_lab_app.skill_import import ImportedSkill
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
    monkeypatch.setattr(
        module, "get_settings", lambda: SimpleNamespace(repo_root=tmp_path)
    )
    monkeypatch.setattr(module, "known_skill_names", lambda: {"brainstorming"})
    return tmp_path


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def _imported(**kwargs) -> ImportedSkill:
    defaults = {
        "name": "probe",
        "description": "probes things",
        "files": {
            "SKILL.md": CONTENT.encode(),
            "scripts/run.sh": b"#!/bin/sh\necho hi\n",
            "references/notes.md": b"notes\n",
        },
        "source_url": "https://github.com/o/r",
        "source_ref": "refs/heads/main",
        "source_kind": "url",
    }
    return ImportedSkill(**{**defaults, **kwargs})


# -- storage ------------------------------------------------------------------


def test_create_stores_one_file_row(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        skill = service.create_skill("probe", "probes", CONTENT)
        rows = service.list_files("probe")
        assert [row.path for row in rows] == ["SKILL.md"]
        assert rows[0].encoding == "utf-8"
        assert rows[0].size == len(CONTENT.encode())
        assert skill.content_hash  # whole-tree digest, computed on write
        assert skill.source_kind == "manual"


def test_import_materializes_the_whole_tree(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        skill = service.import_skill(_imported())
        root = service_env / "data" / "skills_staging" / "probe"
        assert _tree(root) == ["SKILL.md", "references/notes.md", "scripts/run.sh"]
        assert skill.source_url == "https://github.com/o/r"
        assert skill.source_ref == "refs/heads/main"
        assert skill.imported_at is not None
        # The mirror column keeps every existing reader working.
        assert skill.content == CONTENT


def test_import_refuses_to_overwrite_unless_asked(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.import_skill(_imported())
        with pytest.raises(StagedSkillError, match="already exists"):
            service.import_skill(_imported())
        updated = service.import_skill(
            _imported(files={"SKILL.md": CONTENT.encode()}), replace=True
        )
        assert [row.path for row in service.list_files("probe")] == ["SKILL.md"]
        assert _tree(service_env / "data" / "skills_staging" / "probe") == ["SKILL.md"]
        assert updated.name == "probe"


def test_import_rejects_a_governed_name(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        with pytest.raises(StagedSkillError, match="collides"):
            service.import_skill(_imported(), name="brainstorming")


def test_binary_files_round_trip_as_base64(service_env):
    blob = bytes(range(256))
    with _session() as session:
        service = StagedSkillService(session)
        service.import_skill(
            _imported(files={"SKILL.md": CONTENT.encode(), "assets/logo.png": blob})
        )
        row = service.get_file("probe", "assets/logo.png")
        assert row.encoding == "base64"
        staged = service_env / "data" / "skills_staging" / "probe" / "assets/logo.png"
        assert staged.read_bytes() == blob


def test_content_hash_changes_when_a_supporting_file_changes(service_env):
    """The hash must cover the whole tree, or an A/B silently reuses an arm."""
    with _session() as session:
        service = StagedSkillService(session)
        before = service.import_skill(_imported()).content_hash
        service.put_file("probe", "scripts/run.sh", "#!/bin/sh\necho bye\n")
        assert service.get_skill("probe").content_hash != before


# -- per-file editing ---------------------------------------------------------


def test_put_file_adds_and_materializes(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.create_skill("probe", "", CONTENT)
        service.put_file("probe", "scripts/run.sh", "echo hi\n")
        staged = service_env / "data" / "skills_staging" / "probe" / "scripts/run.sh"
        assert staged.read_text() == "echo hi\n"


def test_put_file_rejects_traversal(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.create_skill("probe", "", CONTENT)
        with pytest.raises(StagedSkillError, match="Unsafe file path"):
            service.put_file("probe", "../../escape.md", "x")


def test_delete_file_prunes_from_disk(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.import_skill(_imported())
        service.delete_file("probe", "scripts/run.sh")
        root = service_env / "data" / "skills_staging" / "probe"
        assert _tree(root) == ["SKILL.md", "references/notes.md"]


def test_skill_md_cannot_be_deleted(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.create_skill("probe", "", CONTENT)
        with pytest.raises(StagedSkillError, match="cannot be deleted"):
            service.delete_file("probe", "SKILL.md")


def test_missing_file_is_a_404_shaped_error(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.create_skill("probe", "", CONTENT)
        with pytest.raises(StagedSkillError, match="not found"):
            service.get_file("probe", "nope.md")


# -- reconcile ----------------------------------------------------------------


def test_reconcile_rebuilds_a_wiped_staging_dir(service_env):
    """The acceptance criterion for DB-is-truth: `data/` is disposable."""
    with _session() as session:
        service = StagedSkillService(session)
        service.import_skill(_imported())
        shutil.rmtree(service_env / "data" / "skills_staging")

        assert service.staging_drift() == ["data/skills_staging/probe (missing)"]
        result = service.reconcile_all()

        assert result["rebuilt"] == ["probe"]
        root = service_env / "data" / "skills_staging" / "probe"
        assert _tree(root) == ["SKILL.md", "references/notes.md", "scripts/run.sh"]
        assert service.staging_drift() == []


def test_reconcile_reports_orphan_directories(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.create_skill("probe", "", CONTENT)
        (service_env / "data" / "skills_staging" / "ghost").mkdir()
        assert service.reconcile_all()["orphans"] == ["ghost"]


def test_drift_is_detected_when_disk_is_edited_behind_the_db(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.import_skill(_imported())
        rogue = service_env / "data" / "skills_staging" / "probe" / "rogue.md"
        rogue.write_text("added out of band\n")
        assert service.staging_drift() == ["data/skills_staging/probe (out of date)"]
        service.reconcile_all()
        assert not rogue.exists()


def test_archived_skills_are_left_alone(service_env):
    with _session() as session:
        service = StagedSkillService(session)
        service.create_skill("probe", "", CONTENT)
        service.update_skill("probe", status="archived")
        assert service.reconcile_all()["rebuilt"] == []
        assert service.staging_drift() == []


# -- promotion ----------------------------------------------------------------


def test_promote_copies_every_file(service_env, monkeypatch):
    monkeypatch.setattr(StagedSkillService, "_sync_skill_views", lambda _self: None)
    with _session() as session:
        service = StagedSkillService(session)
        service.import_skill(_imported())
        result = service.promote_skill("probe")
        dest = service_env / ".github" / "skills-external" / "probe"
        assert _tree(dest) == ["SKILL.md", "references/notes.md", "scripts/run.sh"]
        assert len(result["written"]) == 3


def test_linker_projects_every_top_level_item_of_an_external_skill(tmp_path):
    """Regression guard: multi-file skills rely on this linker behavior.

    A promoted multi-file skill is an *external* skill, and the native views
    are only useful if every top-level item — not just SKILL.md — is linked.
    """
    from ml_python_base.skills_sync.linker import _link_symlink
    from ml_python_base.skills_sync.models import (
        KIND_EXTERNAL,
        LINK_SYMLINK,
        Skill,
        ToolSpec,
    )

    source = tmp_path / ".github" / "skills-external" / "probe"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text(CONTENT)
    (source / "scripts" / "run.sh").write_text("echo hi\n")
    (source / "reference.md").write_text("ref\n")

    dest_root = tmp_path / ".claude" / "skills"
    dest_root.mkdir(parents=True)
    tool = ToolSpec(
        id="claude",
        display_name="Claude Code",
        link_strategy=LINK_SYMLINK,
        native_skills_dir=".claude/skills",
    )
    _link_symlink(
        tmp_path,
        dest_root,
        tool,
        Skill(name="probe", kind=KIND_EXTERNAL, source_path=source, description=""),
    )

    destination = dest_root / "probe"
    assert sorted(p.name for p in destination.iterdir()) == [
        "SKILL.md",
        "reference.md",
        "scripts",
    ]
    # The links must resolve, or the native view is decorative.
    assert (destination / "scripts" / "run.sh").read_text() == "echo hi\n"
