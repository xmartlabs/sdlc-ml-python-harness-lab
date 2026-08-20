"""Tests for Phase 4 PR 5 — candidate worktree preparation and locking.

Exercises the mutating-but-isolated worktree flow against real temporary git
repositories: worktree creation off HEAD (main untouched), governance checkout
from a release ref, forbidden-path safety, cleanup, and the advisory file lock.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness_lab_app.harness_lifecycle import errors, locking, sync

_GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(root: Path, *args: str) -> str:
    env = dict(_GIT_ENV)
    env["HOME"] = str(root)
    out = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, env=env
    )
    return out.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _registry(protocol: int) -> str:
    return (
        "schema_version = 1\n\n"
        "[template_sync]\n"
        f"protocol = {protocol}\n"
        'governance_paths = [".github/architecture.md"]\n'
        'platform_paths = ["Makefile", "adapters/registry.toml"]\n'
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """HEAD = installed (protocol 1, gov v1). v0.3.0 = governance-only (protocol 1);
    v0.4.0 = a protocol bump to 2 (platform upgrade, must be refused)."""
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "adapters/registry.toml", _registry(1))
    _write(root, ".github/architecture.md", "# Architecture\nv1\n")
    _write(root, "Makefile", "check:\n\t@echo v1\n")
    _write(root, "src/x.py", "x = 1\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "C1 installed")
    # v0.3.0 = governance-only release (safe to sync).
    _write(root, ".github/architecture.md", "# Architecture\nv2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "C2 governance")
    _git(root, "tag", "-a", "v0.3.0", "-m", "r")
    # v0.4.0 = protocol bump (platform upgrade required).
    _write(root, "adapters/registry.toml", _registry(2))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "C3 protocol bump")
    _git(root, "tag", "-a", "v0.4.0", "-m", "r")
    _git(root, "reset", "-q", "--hard", "HEAD~2")  # back to C1 (installed)
    return root


# --------------------------------------------------------------------------- #
# Locking
# --------------------------------------------------------------------------- #


def test_file_lock_is_exclusive(tmp_path: Path) -> None:
    lock = tmp_path / ".lock"
    with locking.file_lock(lock):  # noqa: SIM117 — must hold while a 2nd acquire fails
        with (
            pytest.raises(errors.LifecycleError) as exc,
            locking.file_lock(lock, timeout=0.2),
        ):
            pass
    assert exc.value.exit_code == errors.EXIT_PRECONDITION


def test_file_lock_reacquire_after_release(tmp_path: Path) -> None:
    lock = tmp_path / ".lock"
    with locking.file_lock(lock):
        pass
    with locking.file_lock(lock, timeout=0.2):  # free again — no raise
        pass


# --------------------------------------------------------------------------- #
# Candidate worktree mechanics
# --------------------------------------------------------------------------- #


def test_create_candidate_worktree_isolated(repo: Path) -> None:
    head_before = _git(repo, "rev-parse", "HEAD")
    path = sync.create_candidate_worktree(repo, "v0.3.0")
    assert path == repo / ".worktrees" / "harness-candidate-v0.3.0"
    assert path.is_dir()
    # Main HEAD and its files are untouched.
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert (repo / ".github/architecture.md").read_text() == "# Architecture\nv1\n"
    # The candidate branch exists and starts at the same commit as main HEAD.
    assert _git(repo, "branch", "--list", "harness-candidate/v0.3.0")


def test_apply_governance_sync_pulls_only_governance(repo: Path) -> None:
    worktree = sync.create_candidate_worktree(repo, "v0.3.0")
    applied = sync.apply_governance_sync(
        worktree, "v0.3.0", [".github/architecture.md"]
    )
    assert applied == [".github/architecture.md"]
    # Governance file advanced to v2 inside the worktree...
    assert (worktree / ".github/architecture.md").read_text() == "# Architecture\nv2\n"
    # ...while the forbidden Makefile stays at the installed v1.
    assert (worktree / "Makefile").read_text() == "check:\n\t@echo v1\n"
    # And main is still on v1.
    assert (repo / ".github/architecture.md").read_text() == "# Architecture\nv1\n"


def test_worktree_changed_paths_and_no_forbidden(repo: Path) -> None:
    worktree = sync.create_candidate_worktree(repo, "v0.3.0")
    sync.apply_governance_sync(worktree, "v0.3.0", [".github/architecture.md"])
    changed = sync.worktree_changed_paths(worktree)
    assert (".github/architecture.md" in [p for _s, p in changed]) is True
    assert sync.forbidden_changes(changed) == []


def test_forbidden_changes_detects_forbidden() -> None:
    entries = [("M", "Makefile"), ("M", ".github/architecture.md"), ("M", "src/a.py")]
    assert set(sync.forbidden_changes(entries)) == {"Makefile", "src/a.py"}


def test_remove_candidate_worktree(repo: Path) -> None:
    path = sync.create_candidate_worktree(repo, "v0.3.0")
    assert path.is_dir()
    sync.remove_candidate_worktree(repo, "v0.3.0")
    assert not path.exists()
    assert _git(repo, "branch", "--list", "harness-candidate/v0.3.0") == ""


def test_main_tree_clean(repo: Path) -> None:
    assert sync.main_tree_clean(repo) is True
    _write(repo, "dirty.txt", "x")
    assert sync.main_tree_clean(repo) is False


# --------------------------------------------------------------------------- #
# CLI orchestration (remote monkeypatched; no network, no adapter engine)
# --------------------------------------------------------------------------- #


def _wire_cli(repo: Path, monkeypatch: pytest.MonkeyPatch):
    from harness_lab_app.harness_lifecycle import cli

    monkeypatch.setattr(cli, "REPO_ROOT", repo)
    monkeypatch.setattr(
        cli.releases, "template_remote_configured", lambda *_a, **_k: True
    )
    monkeypatch.setattr(cli.releases, "fetch_template", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_regenerate_adapters", lambda *_a, **_k: True)
    return cli


def test_cli_sync_branch_prepares_candidate(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _wire_cli(repo, monkeypatch)
    assert cli.main(["sync-branch", "--ref", "v0.3.0"]) == cli.EXIT_OK
    worktree = repo / ".worktrees" / "harness-candidate-v0.3.0"
    assert (worktree / ".github/architecture.md").read_text() == "# Architecture\nv2\n"
    assert (worktree / ".harness-candidate.yaml").is_file()
    # Main worktree is untouched.
    assert (repo / ".github/architecture.md").read_text() == "# Architecture\nv1\n"


def test_cli_sync_branch_refuses_dirty_main(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _wire_cli(repo, monkeypatch)
    _write(repo, "dirty.txt", "x")
    assert cli.main(["sync-branch", "--ref", "v0.3.0"]) == errors.EXIT_PRECONDITION
    assert not (repo / ".worktrees").exists()


def test_cli_sync_branch_refuses_protocol_bump(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _wire_cli(repo, monkeypatch)
    # v0.4.0 bumps the sync protocol → needs a platform upgrade, not a governance sync.
    assert cli.main(["sync-branch", "--ref", "v0.4.0"]) == errors.EXIT_INCOMPATIBLE
    assert not (repo / ".worktrees").exists()
