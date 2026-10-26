"""`make harness-export`: carry the lab's harness, or one skill, into your repo (ADR-0072)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import harness_export

LAB = Path(__file__).resolve().parents[2]


def _git_repo(path: Path, files: dict[str, str] | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name, body in (files or {"README.md": "# mine\n"}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(body)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
    )
    return path


def _snapshot(path: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(path)): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(path).parts
    }


def _a_skill() -> str:
    from ml_python_base.skills_sync.discovery import discover_skills

    return discover_skills(LAB)[0].name


def test_a_preview_never_writes_to_the_repository(tmp_path):
    repo = _git_repo(tmp_path / "mine")
    before = _snapshot(repo)
    plan = harness_export.export(repo, tool="claude", apply=False)
    assert _snapshot(repo) == before
    assert plan.files_written, "a preview names what it would write"
    assert any(f.startswith(".claude/skills/") for f in plan.files_written)


def test_apply_writes_what_the_preview_announced(tmp_path):
    repo = _git_repo(tmp_path / "mine")
    preview = harness_export.export(repo, tool="claude", apply=False)
    applied = harness_export.export(repo, tool="claude", apply=True)
    assert sorted(applied.files_written) == sorted(preview.files_written)
    for rel in applied.files_written:
        assert (repo / rel).exists(), rel


def test_one_skill_carries_only_that_skill(tmp_path):
    repo = _git_repo(tmp_path / "mine")
    name = _a_skill()
    plan = harness_export.export(
        repo, tool="claude", skills=[name], governance=False, apply=True
    )
    assert plan.skills == [name]
    skill_dirs = {p.name for p in (repo / ".claude" / "skills").iterdir()}
    assert skill_dirs == {name}
    assert not (repo / ".github" / "architecture.md").exists()
    # One skill means one skill: no agents, no rules file of the lab's.
    assert not (repo / ".claude" / "agents").exists()
    assert not (repo / "CLAUDE.md").exists()
    assert all(f.startswith(f".claude/skills/{name}/") for f in plan.files_written)


def test_an_unknown_skill_is_refused_with_the_known_names(tmp_path):
    repo = _git_repo(tmp_path / "mine")
    with pytest.raises(harness_export.ExportError, match="no-such-skill"):
        harness_export.export(
            repo, tool="claude", skills=["no-such-skill"], apply=False
        )


def test_existing_governance_is_backed_up_not_lost(tmp_path):
    repo = _git_repo(tmp_path / "mine", {"CLAUDE.md": "# my own rules\n"})
    plan = harness_export.export(repo, tool="claude", apply=True)
    assert "CLAUDE.md" in plan.collisions
    assert (repo / "CLAUDE.md.native.bak").read_text() == "# my own rules\n"


def test_applying_onto_uncommitted_work_is_refused(tmp_path):
    """The export is reviewed and reverted with git; uncommitted work would mix in."""
    repo = _git_repo(tmp_path / "mine")
    (repo / "wip.py").write_text("x = 1\n")
    with pytest.raises(harness_export.ExportError, match="uncommitted"):
        harness_export.export(repo, tool="claude", apply=True)


def test_the_lab_itself_is_never_a_target():
    with pytest.raises(harness_export.ExportError, match="lab"):
        harness_export.export(LAB, tool="claude", apply=False)


def test_a_folder_that_is_not_a_git_repository_is_refused(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    with pytest.raises(harness_export.ExportError, match="git"):
        harness_export.export(folder, tool="claude", apply=False)


def test_skills_only_for_a_tool_with_no_skill_folder_is_refused(tmp_path):
    """copilot reads no native skill folder; a skills-only export would write
    nothing and look like it worked."""
    repo = _git_repo(tmp_path / "mine")
    with pytest.raises(harness_export.ExportError, match="skill folder"):
        harness_export.export(
            repo, tool="copilot", skills=[_a_skill()], governance=False, apply=False
        )


def test_the_available_skills_are_listed_by_name(capsys):
    assert harness_export.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert _a_skill() in out
    assert "README" not in out
