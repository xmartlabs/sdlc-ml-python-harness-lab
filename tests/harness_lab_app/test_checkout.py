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
from fastapi import HTTPException

from harness_lab_app import checkout_service, config
from harness_lab_app.api import create_app
from harness_lab_app.checkout_service import (
    CheckoutError,
    create_checkout_from,
    guided_command,
    host_checkout_path_for,
    inspect_checkout,
    list_checkouts,
    local_copy_command,
    repo_context,
    resolve_local_ref,
    validate_target_id,
)
from harness_lab_app.models import TargetRepository
from harness_lab_app.schemas import TargetRepositoryCreate
from harness_lab_app.target_service import (
    TargetError,
    TargetService,
    attach_checkout,
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


def _endpoint(path: str, method: str = "GET"):
    """The route function the API would call for this path.

    The endpoints are exercised directly, as the rest of the API tests do: what
    is worth asserting here is the shape the browser receives, which is the one
    thing the service-level tests cannot see.
    """
    for route in create_app().router.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


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

    assert resolve_local_ref(_target(), "spike") == head
    with pytest.raises(CheckoutError):
        resolve_local_ref(_target(), "no-such-branch")


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


# --- A checkout that already existed, under whatever name (ADR-0045) ---------


def test_inspect_reads_a_checkout_attached_under_another_name(lab_root: Path):
    """The directory is named after the clone, not after the target that wants it.

    One repository is often cloned once and audited twice, and an operator who
    already has it under an older id must not be asked for a second copy of a
    client's code.
    """
    path = _make_checkout(lab_root, target_id="payments-legacy")
    head = _git(["rev-parse", "HEAD"], path)

    state = inspect_checkout(_target(checkout_path=str(path)))

    assert state.is_repo is True
    assert state.head == head
    assert state.path == str(path)
    # The page says so, because otherwise the path on screen reads as a typo.
    assert state.attached is True


def test_a_conventional_checkout_is_not_reported_as_attached(lab_root: Path):
    path = _make_checkout(lab_root)
    assert inspect_checkout(_target(checkout_path=str(path))).attached is False


def test_a_recorded_path_outside_the_checkouts_directory_is_ignored(lab_root: Path):
    """The fence of ADR-0042 holds even against a row edited by hand.

    Falling back to the convention makes such a target read as "not cloned
    yet" — true, and recoverable — rather than reading somebody's /etc.
    """
    state = inspect_checkout(_target(checkout_path="/etc"))
    assert state.path == str(lab_root / "data" / "checkouts" / "payments")
    assert state.exists is False


def test_attach_points_a_target_at_an_existing_clone(lab_root: Path, session):
    path = _make_checkout(lab_root, target_id="payments-legacy")
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://example.invalid/payments.git",
        )
    )

    target = attach_checkout(session, "payments", "payments-legacy")

    assert target.checkout_path == str(path)
    # Attaching is not pinning: the commit stays a separate, deliberate act.
    assert target.pinned_commit == ""


def test_attach_refuses_anything_outside_the_checkouts_directory(
    lab_root: Path, session
):
    _make_checkout(lab_root, target_id="payments-legacy")
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://example.invalid/payments.git",
        )
    )
    for bad in ("../checkouts/payments-legacy", "/etc", "", "not-cloned"):
        with pytest.raises(CheckoutError):
            attach_checkout(session, "payments", bad)


def test_pinning_does_not_move_an_attached_checkout(lab_root: Path, session):
    """The regression this ordering exists to prevent.

    ``pin_checkout`` writes back the path it inspected, so an inspect that
    rebuilt the conventional path would silently un-attach the clone the
    operator chose and pin nothing.
    """
    path = _make_checkout(lab_root, target_id="payments-legacy")
    head = _git(["rev-parse", "HEAD"], path)
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://example.invalid/payments.git",
        )
    )
    attach_checkout(session, "payments", "payments-legacy")

    target = pin_checkout(session, "payments")

    assert target.checkout_path == str(path)
    assert target.pinned_commit == head


def test_list_checkouts_reports_every_clone_whoever_made_it(lab_root: Path):
    _make_checkout(lab_root, target_id="payments")
    _make_checkout(lab_root, target_id="ledger")
    # Not a repository, and a hidden directory: neither is a choice to offer.
    (lab_root / "data" / "checkouts" / "scratch").mkdir()
    (lab_root / "data" / "checkouts" / ".cache").mkdir()

    rows = list_checkouts()

    assert [row["name"] for row in rows] == ["ledger", "payments"]
    assert all(row["branch"] == "main" for row in rows)
    assert all(len(str(row["head"])) == 40 for row in rows)


def test_list_checkouts_is_empty_before_anything_is_cloned(lab_root: Path):
    assert list_checkouts() == []


# --- Copying a repository the operator already has ---------------------------


def test_the_local_copy_command_never_hardlinks_and_quotes_its_paths(
    lab_root: Path, monkeypatch
):
    """Hardlinks would couple the lab's copy to the operator's object store.

    That is exactly the coupling ADR-0042 avoids, and it costs nothing to
    refuse: the copy is local, so there is no network either way.
    """
    settings = config.Settings(repo_root=Path("/app"), host_repo_root="/home/dev/lab")
    monkeypatch.setattr(checkout_service, "get_settings", lambda: settings)

    command = local_copy_command("payments", "/home/dev/my repos/payments/")

    assert command == (
        "git clone --no-hardlinks '/home/dev/my repos/payments' "
        "/home/dev/lab/data/checkouts/payments"
    )
    with pytest.raises(CheckoutError):
        local_copy_command("payments", "relative/path")
    with pytest.raises(CheckoutError):
        local_copy_command("payments", "")


def test_copying_a_local_repository_leaves_the_original_untouched(lab_root: Path):
    source = lab_root / "elsewhere" / "payments"
    source.mkdir(parents=True)
    _git(["init", "--quiet", "-b", "main"], source)
    (source / "README.md").write_text("# Payments\n")
    _git(["add", "-A"], source)
    _git(["commit", "--quiet", "-m", "first"], source)
    head = _git(["rev-parse", "HEAD"], source)
    # Uncommitted work is not copied — git clone reads commits.
    (source / "scratch.txt").write_text("not committed\n")

    destination = create_checkout_from(source, _target())

    assert destination == lab_root / "data" / "checkouts" / "payments"
    assert _git(["rev-parse", "HEAD"], destination) == head
    assert (destination / "scratch.txt").exists() is False
    assert (source / "scratch.txt").exists() is True
    assert inspect_checkout(_target()).head == head


def test_a_copied_checkout_points_at_the_repository_of_record(lab_root: Path):
    """Not at the mount the copy came through.

    The source is read via a bind mount, so the origin git records is a path
    that means nothing outside the API container and disappears when the mount
    changes. The declared repository URL is what the guided clone would have
    left there.
    """
    source = lab_root / "elsewhere" / "payments"
    source.mkdir(parents=True)
    _git(["init", "--quiet", "-b", "main"], source)
    (source / "README.md").write_text("# Payments\n")
    _git(["add", "-A"], source)
    _git(["commit", "--quiet", "-m", "first"], source)

    create_checkout_from(source, _target())

    assert inspect_checkout(_target()).remote_url == (
        "https://example.invalid/payments.git"
    )


def test_copying_refuses_a_source_that_is_not_a_repository(lab_root: Path):
    source = lab_root / "elsewhere" / "notes"
    source.mkdir(parents=True)
    with pytest.raises(CheckoutError):
        create_checkout_from(source, _target())


def test_copying_never_overwrites_a_checkout_that_is_already_there(lab_root: Path):
    """The existing directory may be somebody's work in progress."""
    source = lab_root / "elsewhere" / "payments"
    source.mkdir(parents=True)
    _git(["init", "--quiet", "-b", "main"], source)
    (source / "README.md").write_text("# Payments\n")
    _git(["add", "-A"], source)
    _git(["commit", "--quiet", "-m", "first"], source)
    _make_checkout(lab_root)

    with pytest.raises(CheckoutError) as error:
        create_checkout_from(source, _target())
    assert "Attach it instead" in str(error.value)


# --- What the browser actually receives -------------------------------------


def test_the_checkouts_endpoint_answers_with_a_plain_list(lab_root: Path, session):
    """A wrapper object here would leave the picker permanently empty.

    The page renders the response as an array, and a mismatch fails silently:
    no error, no list, and an operator told to clone a repository they already
    have. Nothing at the service level can catch that.
    """
    _make_checkout(lab_root, target_id="payments-legacy")

    rows = _endpoint("/api/checkouts")(session)

    assert isinstance(rows, list)
    assert [row["name"] for row in rows] == ["payments-legacy"]


def test_the_checkouts_endpoint_says_which_targets_already_use_a_clone(
    lab_root: Path, session
):
    """Sharing one clone between targets is allowed; doing it unaware is not."""
    _make_checkout(lab_root, target_id="payments-legacy")
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://example.invalid/payments.git",
        )
    )
    attach_checkout(session, "payments", "payments-legacy")

    rows = _endpoint("/api/checkouts")(session)

    assert rows[0]["claimed_by"] == ["payments"]


def test_attaching_reports_an_unknown_target_and_a_missing_clone_apart(
    lab_root: Path, session
):
    from harness_lab_app.schemas import CheckoutAttachRequest

    attach = _endpoint("/api/targets/{target_id}/checkout/attach", "POST")
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://example.invalid/payments.git",
        )
    )

    with pytest.raises(HTTPException) as missing_target:
        attach("ghost", CheckoutAttachRequest(name="payments-legacy"), session)
    assert missing_target.value.status_code == 404

    with pytest.raises(HTTPException) as missing_clone:
        attach("payments", CheckoutAttachRequest(name="not-cloned"), session)
    assert missing_clone.value.status_code == 422


def test_browsing_local_sources_is_an_answer_when_the_picker_is_off(lab_root: Path):
    """Off is the default, so it must not read as a failure.

    The page needs to know it can hide the picker and explain the setting; a
    422 would show an operator an error for a feature they never enabled.
    """
    listing = _endpoint("/api/local-sources")("")

    assert listing["enabled"] is False
    assert listing["entries"] == []
    assert "HARNESS_LAB_LOCAL_SOURCES_DIR" in listing["reason"]


def test_the_checkout_endpoint_tells_the_page_whether_the_picker_exists(
    lab_root: Path, session
):
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://example.invalid/payments.git",
        )
    )

    body = _endpoint("/api/targets/{target_id}/checkout")("payments", session)

    assert body["local_sources_enabled"] is False
    assert body["attached"] is False
    assert body["command"].startswith("git clone ")
