"""The operator's own checkout of a target repository (ADR-0042).

The properties under test are the ones that make the audit flow worth having: a
repository is read from the engineer's clone rather than over the network, a pin
freezes it at one commit, and a target registered before checkouts existed keeps
working exactly as it did.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness_lab_app import checkout_service, config
from harness_lab_app.checkout_service import (
    CheckoutError,
    guided_command,
    host_checkout_path_for,
    inspect_checkout,
    repo_context,
    resolve_local_ref,
    validate_target_id,
)
from harness_lab_app.models import TargetRepository
from harness_lab_app.schemas import TargetRepositoryCreate
from harness_lab_app.target_service import (
    TargetError,
    TargetService,
    clone_source,
    pin_checkout,
    validate_clone_source,
)

_GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_AUTHOR_NAME": "Lab",
    "GIT_AUTHOR_EMAIL": "lab@example.invalid",
    "GIT_COMMITTER_NAME": "Lab",
    "GIT_COMMITTER_EMAIL": "lab@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_GIT_ENV,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def lab_root(monkeypatch, tmp_path: Path) -> Path:
    """A lab root whose data/checkouts is a scratch directory."""
    settings = config.Settings(repo_root=tmp_path)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(checkout_service, "get_settings", lambda: settings)
    return tmp_path


def _make_checkout(lab_root: Path, target_id: str = "payments") -> Path:
    """A real git repository where the lab expects the operator's clone."""
    path = lab_root / "data" / "checkouts" / target_id
    path.mkdir(parents=True)
    _git(["init", "--quiet", "-b", "main"], path)
    (path / "README.md").write_text("# Payments\n\nHandles money.\n")
    (path / "CLAUDE.md").write_text("Run `make check` before finishing.\n")
    (path / "tests").mkdir()
    (path / "tests" / "test_smoke.py").write_text(
        "def test_smoke():\n    assert True\n"
    )
    _git(["add", "-A"], path)
    _git(["commit", "--quiet", "-m", "first"], path)
    return path


def _target(**over) -> TargetRepository:
    base = {
        "target_id": "payments",
        "name": "Payments",
        "repository_url": "https://example.invalid/payments.git",
        "default_ref": "main",
    }
    base.update(over)
    return TargetRepository(**base)


def test_target_id_may_not_escape_the_checkouts_directory():
    """A target id becomes a directory name, so it is a path boundary."""
    for bad in ("../escape", "/absolute", ".hidden", "with space", ""):
        with pytest.raises(CheckoutError):
            validate_target_id(bad)
    assert validate_target_id(" payments-api.v2 ") == "payments-api.v2"


def test_guided_command_points_at_the_host_path(lab_root: Path, monkeypatch):
    """The command runs in the operator's terminal, so it needs the host path.

    The API's own view (/app/...) is meaningless outside its container.
    """
    settings = config.Settings(repo_root=Path("/app"), host_repo_root="/home/dev/lab")
    monkeypatch.setattr(checkout_service, "get_settings", lambda: settings)
    command = guided_command(_target())
    assert command == (
        "git clone https://example.invalid/payments.git "
        "/home/dev/lab/data/checkouts/payments"
    )


def test_guided_command_checks_out_a_non_default_ref(lab_root: Path):
    command = guided_command(_target(default_ref="release/2.0"))
    assert command.endswith("checkout release/2.0")


def test_inspect_reports_not_cloned_as_an_ordinary_state(lab_root: Path):
    """Before the operator has run the command, "nothing there" is the answer."""
    state = inspect_checkout(_target())
    assert state.exists is False
    assert state.is_repo is False
    assert state.error == ""


def test_inspect_reads_head_branch_and_remote(lab_root: Path):
    path = _make_checkout(lab_root)
    head = _git(["rev-parse", "HEAD"], path)

    state = inspect_checkout(_target())

    assert state.is_repo is True
    assert state.head == head
    assert state.branch == "main"
    assert state.dirty is False


def test_inspect_flags_a_directory_that_is_not_a_repository(lab_root: Path):
    (lab_root / "data" / "checkouts" / "payments").mkdir(parents=True)
    state = inspect_checkout(_target())
    assert state.exists is True
    assert state.is_repo is False
    assert "not a git repository" in state.error


def test_a_dirty_tree_is_reported_but_is_not_an_error(lab_root: Path):
    """Uncommitted work never enters a run — the runner clones git objects.

    So it is a warning the page shows, not a reason to refuse the pin.
    """
    path = _make_checkout(lab_root)
    (path / "README.md").write_text("# Payments\n\nEdited but not committed.\n")

    state = inspect_checkout(_target())

    assert state.dirty is True
    assert state.dirty_files == 1
    assert state.error == ""


def test_pin_freezes_the_target_at_head(lab_root: Path, session):
    path = _make_checkout(lab_root)
    head = _git(["rev-parse", "HEAD"], path)
    service = TargetService(session)
    service.create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://example.invalid/payments.git",
        )
    )

    target = pin_checkout(session, "payments")

    assert target.pinned_commit == head
    assert target.checkout_path == str(path)
    assert target.pinned_at is not None


def test_pin_refuses_when_nothing_has_been_cloned(lab_root: Path, session):
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://example.invalid/payments.git",
        )
    )
    with pytest.raises(CheckoutError):
        pin_checkout(session, "payments")


def test_pin_moves_when_the_operator_pulls(lab_root: Path, session):
    """Re-pinning is how "we audited them again this month" stays honest."""
    path = _make_checkout(lab_root)
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://example.invalid/payments.git",
        )
    )
    first = pin_checkout(session, "payments").pinned_commit

    (path / "README.md").write_text("# Payments\n\nMore.\n")
    _git(["add", "-A"], path)
    _git(["commit", "--quiet", "-m", "second"], path)
    state = inspect_checkout(TargetService(session).get_target("payments"))
    assert state.pin_is_stale is True

    second = pin_checkout(session, "payments").pinned_commit
    assert second != first


def test_clone_source_prefers_the_checkout_and_falls_back_to_the_remote(
    lab_root: Path,
):
    """A target registered before checkouts existed keeps reading the remote."""
    path = _make_checkout(lab_root)
    assert clone_source(_target()) == "https://example.invalid/payments.git"
    assert clone_source(_target(checkout_path=str(path))) == str(path)
    # A path recorded once but since deleted must not silently produce a run
    # against an empty directory.
    assert (
        clone_source(_target(checkout_path=str(lab_root / "gone")))
        == "https://example.invalid/payments.git"
    )


def test_a_local_source_is_only_accepted_inside_the_checkouts_directory(
    lab_root: Path, monkeypatch
):
    """Otherwise a target id could name any git repository on the machine."""
    monkeypatch.setattr(
        "harness_lab_app.config.get_settings",
        lambda: config.Settings(repo_root=lab_root),
    )
    path = _make_checkout(lab_root)
    assert validate_clone_source(str(path)) == str(path)
    with pytest.raises(TargetError):
        validate_clone_source("/etc")
    with pytest.raises(TargetError):
        validate_clone_source(str(lab_root / "data" / "checkouts"))


def test_local_refs_resolve_without_the_network(lab_root: Path):
    """A branch the operator made locally is resolvable; the remote never sees it."""
    path = _make_checkout(lab_root)
    _git(["checkout", "--quiet", "-b", "spike"], path)
    (path / "spike.txt").write_text("x\n")
    _git(["add", "-A"], path)
    _git(["commit", "--quiet", "-m", "spike"], path)
    head = _git(["rev-parse", "HEAD"], path)

    assert resolve_local_ref("payments", "spike") == head
    with pytest.raises(CheckoutError):
        resolve_local_ref("payments", "no-such-branch")


def test_repo_context_describes_the_checkout_for_the_draft_assistant(lab_root: Path):
    path = _make_checkout(lab_root)
    (path / "node_modules").mkdir()

    context = repo_context(
        _target(checkout_path=str(path), validation_commands=["make check"])
    )

    assert "# Payments" in context.readme
    assert context.rules_file == "CLAUDE.md"
    assert "tests/" in context.test_dirs
    assert any("node_modules" in entry for entry in context.tree) is False
    block = context.as_prompt_block()
    assert "make check" in block
    assert "Repository context" in block


def test_repo_context_is_empty_when_nothing_is_cloned(lab_root: Path):
    context = repo_context(_target(validation_commands=["make check"]))
    assert context.readme == ""
    # The adopted commands are still worth telling the assistant about.
    assert "make check" in context.as_prompt_block()


def test_the_guided_command_never_prints_the_container_s_own_path(monkeypatch):
    """A path only the API container can see is worse than useless in a shell.

    It looks valid and creates the wrong directory on the operator's machine, so
    the API must be given the host path (docker-compose sets
    HARNESS_LAB_HOST_REPO_ROOT on the api service for exactly this).
    """
    settings = config.Settings(repo_root=Path("/app"), host_repo_root="")
    monkeypatch.setattr(checkout_service, "get_settings", lambda: settings)
    with pytest.raises(CheckoutError):
        host_checkout_path_for("payments")
