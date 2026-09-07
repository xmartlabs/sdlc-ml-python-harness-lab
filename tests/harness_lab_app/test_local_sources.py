"""Browsing the operator's own repositories (ADR-0045).

This is the only place the lab lists directories of the host, so the properties
under test are the ones that keep it from becoming "the lab reads your disk":
nothing outside the configured root is reachable — including through a symlink,
which a textual check would have missed — and the root is a real directory on
somebody's machine, full of things that are not folders and cannot even be
stat'ed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness_lab_app import config, local_sources_service
from harness_lab_app.local_sources_service import (
    LocalSourcesDisabled,
    LocalSourcesError,
    browse,
    is_enabled,
    resolve,
)


@pytest.fixture
def sources_root(monkeypatch, tmp_path: Path) -> Path:
    """A mounted directory of the operator's own repositories."""
    root = tmp_path / "mounted"
    root.mkdir()
    settings = config.Settings(
        repo_root=tmp_path,
        local_sources_root=str(root),
        host_local_sources_root="/Users/dev/code",
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(local_sources_service, "get_settings", lambda: settings)
    return root


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    return path


def test_the_picker_is_off_until_a_root_is_configured(monkeypatch, tmp_path: Path):
    """Unset, no part of the host becomes browsable and the UI hides the picker."""
    settings = config.Settings(repo_root=tmp_path)
    monkeypatch.setattr(local_sources_service, "get_settings", lambda: settings)

    assert is_enabled() is False
    with pytest.raises(LocalSourcesDisabled):
        browse("")


def test_a_container_path_alone_does_not_enable_it(monkeypatch, tmp_path: Path):
    """The host path is what the picker prints, so without it the feature lies.

    docker-compose always sets the container side; the host side is the operator
    opting in.
    """
    settings = config.Settings(
        repo_root=tmp_path, local_sources_root=str(tmp_path), host_local_sources_root=""
    )
    monkeypatch.setattr(local_sources_service, "get_settings", lambda: settings)
    assert is_enabled() is False


def test_browse_lists_directories_and_says_which_are_repositories(sources_root: Path):
    _repo(sources_root / "payments")
    (sources_root / "notes").mkdir()
    (sources_root / "README.md").write_text("not a directory\n")
    (sources_root / "node_modules").mkdir()
    (sources_root / ".cache").mkdir()

    listing = browse("")

    assert listing["enabled"] is True
    assert listing["path"] == ""
    assert listing["parent"] is None
    assert listing["host_path"] == "/Users/dev/code"
    assert listing["entries"] == [
        {"name": "notes", "path": "notes", "is_repo": False},
        {"name": "payments", "path": "payments", "is_repo": True},
    ]


def test_browse_descends_one_level_and_can_come_back_up(sources_root: Path):
    _repo(sources_root / "clients" / "acme.co" / "payments")

    listing = browse("clients/acme.co")

    assert listing["path"] == "clients/acme.co"
    # A directory name containing a dot is a directory name, not a hint.
    assert listing["parent"] == "clients"
    assert listing["host_path"] == "/Users/dev/code/clients/acme.co"
    assert listing["entries"] == [
        {"name": "payments", "path": "clients/acme.co/payments", "is_repo": True}
    ]
    assert browse("clients")["parent"] == ""
    assert browse("clients/acme.co/payments")["is_repo"] is True


def test_nothing_outside_the_root_is_reachable(sources_root: Path, tmp_path: Path):
    _repo(tmp_path / "private")
    for escape in ("..", "../private", "/etc", "clients/../../private"):
        with pytest.raises(LocalSourcesError):
            resolve(escape)


def test_a_symlink_out_of_the_root_is_refused(sources_root: Path, tmp_path: Path):
    """Resolution happens before the check for exactly this case.

    A textual check would accept `escape/` and then read whatever the container
    can see through it.
    """
    outside = _repo(tmp_path / "private")
    (sources_root / "escape").symlink_to(outside)
    with pytest.raises(LocalSourcesError):
        resolve("escape")


def test_a_file_is_not_a_directory_to_browse(sources_root: Path):
    (sources_root / "README.md").write_text("hello\n")
    with pytest.raises(LocalSourcesError):
        resolve("README.md")


def test_an_entry_that_cannot_be_stated_is_skipped(monkeypatch, sources_root: Path):
    """The default root is the operator's home, and a home is not a fixture.

    It holds unix sockets over a shared mount, dead symlinks and directories the
    container may not stat; `Path.is_dir` raises `OSError` on each. One of them
    beside the repositories used to turn the whole listing into a 500.
    """
    _repo(sources_root / "payments")
    (sources_root / "agent.sock").touch()
    real_is_dir = Path.is_dir

    def refusing_is_dir(self: Path, *args, **kwargs):
        if self.name == "agent.sock":
            raise OSError(95, "Operation not supported")
        return real_is_dir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_dir", refusing_is_dir)

    assert [entry["name"] for entry in browse("")["entries"]] == ["payments"]


def test_a_directory_that_cannot_be_read_says_so(sources_root: Path):
    """A 403 of the filesystem's own is an answer; a 500 is not."""
    locked = sources_root / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(LocalSourcesError, match="cannot be listed"):
            browse("locked")
    finally:
        locked.chmod(0o755)


def test_a_worktree_pointing_at_its_repository_still_reads_as_one(sources_root: Path):
    """`.git` is a file, not a directory, in a worktree or a submodule."""
    checkout = sources_root / "payments-hotfix"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: /elsewhere/.git/worktrees/hotfix\n")

    assert browse("")["entries"] == [
        {"name": "payments-hotfix", "path": "payments-hotfix", "is_repo": True}
    ]


def test_the_host_path_is_printed_without_the_dots_it_was_configured_with(
    monkeypatch, sources_root: Path
):
    """The default root is expressed relative to the lab's own directory.

    That makes the raw value `/somewhere/the-lab/..`, which is a correct path
    and a useless thing to read on screen or paste into a terminal.
    """
    settings = config.Settings(
        repo_root=sources_root.parent,
        local_sources_root=str(sources_root),
        host_local_sources_root="/Users/dev/code/the-lab/..",
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(local_sources_service, "get_settings", lambda: settings)
    _repo(sources_root / "payments")

    listing = browse("payments")

    assert listing["root"] == "/Users/dev/code"
    assert listing["host_path"] == "/Users/dev/code/payments"


def _real_repo(path: Path, remote: str) -> Path:
    """A repository git will actually answer questions about."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "trunk", str(path)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", remote],
        check=True,
        capture_output=True,
    )
    return path


def test_a_picked_repository_reports_its_own_remote_and_branch(sources_root: Path):
    """The registration form asks for a URL and a ref the folder already knows.

    Reading them off the folder is what lets picking one be the whole of step 1
    instead of a form the operator fills in from memory.
    """
    _real_repo(sources_root / "payments", "git@github.com:acme/payments.git")

    listing = browse("payments")

    assert listing["is_repo"] is True
    assert listing["remote_url"] == "git@github.com:acme/payments.git"
    assert listing["branch"] == "trunk"


def test_a_folder_with_no_remote_is_still_pickable(sources_root: Path):
    """A repository that was never pushed anywhere answers with blanks.

    Not an error: the operator supplies the URL, and everything else about the
    pick still works.
    """
    _real_repo(sources_root / "scratch", "")
    subprocess.run(
        ["git", "-C", str(sources_root / "scratch"), "remote", "remove", "origin"],
        check=True,
        capture_output=True,
    )

    listing = browse("scratch")

    assert listing["is_repo"] is True
    assert listing["remote_url"] == ""


def test_a_directory_that_is_not_a_repository_is_not_interrogated(sources_root: Path):
    """No git call, and the fields say plainly there is nothing to read."""
    (sources_root / "notes").mkdir()

    listing = browse("notes")

    assert listing["is_repo"] is False
    assert listing["remote_url"] == ""
    assert listing["branch"] == ""
