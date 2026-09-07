"""The operator's own clone of a target repository (ADR-0042).

The lab used to clone a target inside the run container, with
``GIT_TERMINAL_PROMPT=0`` and no credentials — which works for public
repositories and fails immediately for every private one. The alternative is
not to teach the lab about credentials: it is to let the engineer clone the
repository in their own terminal, where their ssh agent and tokens already
work, and have the lab read that clone.

So this module owns three things: the command the UI hands the engineer, the
detection of the clone once it appears, and the pin that freezes it at a
commit. Everything downstream (runs, readiness, case derivation) then reads a
local path instead of a URL, which also means no run ever reaches the network
for source.

Nothing here writes to the checkout. It is the engineer's working copy, and a
lab that pulled, checked out or cleaned it would be editing someone's desk.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from harness_lab_app.config import get_settings

if TYPE_CHECKING:
    from harness_lab_app.models import TargetRepository

# A target_id becomes a directory name, so it is a path boundary: `../..` or an
# absolute-looking id would put the clone anywhere on the host. Ids are operator
# authored and already slug-shaped in practice; this refuses the rest rather
# than sanitising it, because a silently rewritten id would not match the
# directory the operator was told to clone into.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

# Where the lab root is mounted inside its own containers (docker-compose).
_CONTAINER_ROOT = Path("/app")

_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
    "GIT_ASKPASS": "",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}

# Caps for the repository context handed to the case-draft assistant. Generous
# enough to describe a repository, small enough that a monorepo cannot push the
# actual instruction out of the model's attention.
_README_CHARS = 4000
_RULES_CHARS = 3000
_TREE_ENTRIES = 120

_README_NAMES = ("README.md", "README.rst", "README.txt", "README")
_RULES_NAMES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", "OPENCODE.md")
_IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    ".next",
}


class CheckoutError(ValueError):
    """A checkout could not be read or pinned as asked."""


@dataclass
class CheckoutState:
    """What the lab can see of the operator's clone, right now.

    Every field is observed. ``exists`` false and ``is_repo`` false are ordinary
    states, not errors: before the operator has run the command they are exactly
    what the page should say.
    """

    target_id: str
    path: str
    host_path: str
    exists: bool = False
    is_repo: bool = False
    head: str = ""
    branch: str = ""
    remote_url: str = ""
    dirty: bool = False
    dirty_files: int = 0
    error: str = ""
    # True when the target points at a checkout whose directory is not named
    # after it — an operator attached a clone that already existed. The UI says
    # so, because otherwise the path on screen looks like a typo.
    attached: bool = False
    # What the target row currently holds, so the UI can tell "cloned" from
    # "cloned and pinned" and offer a re-pin when HEAD has moved on.
    pinned_commit: str = ""
    pinned_at: str = ""

    @property
    def pin_is_stale(self) -> bool:
        return bool(
            self.pinned_commit and self.head and self.pinned_commit != self.head
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "path": self.path,
            "host_path": self.host_path,
            "exists": self.exists,
            "is_repo": self.is_repo,
            "head": self.head,
            "branch": self.branch,
            "remote_url": self.remote_url,
            "dirty": self.dirty,
            "dirty_files": self.dirty_files,
            "error": self.error,
            "attached": self.attached,
            "pinned_commit": self.pinned_commit,
            "pinned_at": self.pinned_at,
            "pin_is_stale": self.pin_is_stale,
        }


@dataclass
class RepoContext:
    """A short, capped description of a repository for the draft assistant."""

    readme: str = ""
    rules_file: str = ""
    rules_body: str = ""
    tree: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)
    setup_commands: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)

    def as_prompt_block(self) -> str:
        """Render as the prompt section the case assistant reads."""
        sections: list[str] = []
        if self.tree:
            sections.append("Top-level layout:\n" + "\n".join(self.tree))
        if self.test_dirs:
            sections.append("Test locations: " + ", ".join(self.test_dirs))
        if self.validation_commands:
            sections.append(
                "The repository's own validation commands (prefer these verbatim "
                "as validation_commands):\n"
                + "\n".join(f"  {command}" for command in self.validation_commands)
            )
        if self.setup_commands:
            sections.append(
                "Setup commands the lab runs before the agent:\n"
                + "\n".join(f"  {command}" for command in self.setup_commands)
            )
        if self.rules_body:
            sections.append(f"{self.rules_file}:\n{self.rules_body}")
        if self.readme:
            sections.append(f"README (truncated):\n{self.readme}")
        if not sections:
            return ""
        return "Repository context\n" + "\n\n".join(sections)


def validate_target_id(target_id: str) -> str:
    cleaned = (target_id or "").strip()
    if not _SAFE_ID.match(cleaned):
        raise CheckoutError(
            "Target id must be 1-100 characters of letters, digits, '.', '_' or "
            f"'-' and start with a letter or digit — got {target_id!r}."
        )
    return cleaned


def checkout_path_for(target_id: str) -> Path:
    """Where the lab expects a *new* clone of this target to land."""
    return get_settings().checkouts_dir / validate_target_id(target_id)


def recorded_checkout_path(target: TargetRepository) -> Path | None:
    """The checkout the target points at, when it points at a usable one.

    A recorded path outside ``data/checkouts`` is ignored rather than honoured:
    that directory is the fence the whole feature rests on (ADR-0042), and a row
    edited by hand must not be able to reach past it. Falling back to the
    convention makes such a target read as "not cloned yet", which is both true
    and recoverable.
    """
    recorded = (target.checkout_path or "").strip()
    if not recorded:
        return None
    root = get_settings().checkouts_dir
    candidate = Path(recorded)
    if not candidate.is_absolute() or root not in candidate.parents:
        return None
    return candidate


def resolve_checkout_path(target: TargetRepository) -> Path:
    """Where this target's checkout actually is.

    The directory an operator attached when there is one, the conventional
    ``data/checkouts/<target_id>`` otherwise. Every reader goes through here, so
    a clone that already existed under a different name is as good as one made
    by the guided command.
    """
    return recorded_checkout_path(target) or checkout_path_for(target.target_id)


def host_view_of(path: Path) -> str:
    """The same location as the *host* sees it.

    The API runs in a container that mounts the lab root at /app, so the path it
    would print is meaningless in the operator's terminal. Runner containers
    already solve this with ``host_repo_root`` (see ``tasks.py``); the guided
    commands use the same value.
    """
    settings = get_settings()
    root = settings.host_repo_root or str(settings.repo_root)
    if not settings.host_repo_root and Path(root) == _CONTAINER_ROOT:
        # `/app` with no host path configured means we are inside the container
        # and cannot know where the repository lives on the host. Printing it
        # anyway would be worse than an error: the command looks valid and
        # silently creates `/app/data/checkouts/...` on the operator's machine.
        raise CheckoutError(
            "This API container does not know its host path, so it cannot write "
            "a clone command you could run. Set HARNESS_LAB_HOST_REPO_ROOT on "
            "the api service (docker-compose sets it to $PWD) and restart it."
        )
    try:
        relative = path.relative_to(settings.repo_root)
    except ValueError:
        # Already outside the lab root, so there is nothing to translate.
        return str(path)
    return str(Path(root) / relative)


def host_checkout_path_for(target_id: str) -> str:
    """Where a new clone of this target should land, as the host sees it."""
    return host_view_of(checkout_path_for(target_id))


def guided_command(target: TargetRepository) -> str:
    """The exact command the operator pastes into their own terminal."""
    destination = host_checkout_path_for(target.target_id)
    command = f"git clone {target.repository_url} {destination}"
    ref = (target.default_ref or "").strip()
    if ref and ref not in ("main", "master"):
        command += f" && git -C {destination} checkout {ref}"
    return command


def local_copy_command(target_id: str, source: str) -> str:
    """The command that copies a repository the operator already has.

    ``--no-hardlinks`` on purpose: git would otherwise link the new checkout's
    objects to the operator's own repository, which is exactly the coupling
    ADR-0042 avoids — the lab must never be able to touch their working copy.
    It costs a copy of the object store and no network at all.
    """
    cleaned = (source or "").strip()
    if not cleaned:
        raise CheckoutError("A path to the repository you already have is required.")
    if not cleaned.startswith("/"):
        raise CheckoutError(
            f"Use the absolute path of the repository — got {cleaned!r}."
        )
    destination = host_checkout_path_for(target_id)
    return (
        f"git clone --no-hardlinks {shlex.quote(cleaned.rstrip('/'))} "
        f"{shlex.quote(destination)}"
    )


def _git(args: list[str], cwd: Path, timeout: int = 30) -> tuple[int, str]:
    try:
        # List form with a fixed env; `cwd` is built from a validated target id,
        # so nothing here reaches a shell or is read by git as an option.
        result = subprocess.run(  # nosec B603 - list form, fixed env
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_GIT_ENV,
            check=False,
        )
    except FileNotFoundError:  # pragma: no cover - git is in every image
        return 127, "git is not available"
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]} timed out after {timeout}s"
    output = (result.stdout or "") if result.returncode == 0 else (result.stderr or "")
    return result.returncode, output.strip()


def inspect_checkout(target: TargetRepository) -> CheckoutState:
    """Read the operator's clone without touching it."""
    path = resolve_checkout_path(target)
    state = CheckoutState(
        target_id=target.target_id,
        path=str(path),
        host_path=host_view_of(path),
        attached=path != checkout_path_for(target.target_id),
        pinned_commit=target.pinned_commit,
        pinned_at=target.pinned_at.isoformat() if target.pinned_at else "",
    )
    if not path.exists():
        return state
    state.exists = True
    if not (path / ".git").exists():
        state.error = (
            "The directory exists but is not a git repository. Remove it and run "
            "the clone command again."
        )
        return state
    code, head = _git(["rev-parse", "HEAD"], path)
    if code != 0:
        state.error = head or "Could not read HEAD."
        return state
    state.is_repo = True
    state.head = head
    _, state.branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    code, remote = _git(["remote", "get-url", "origin"], path)
    state.remote_url = remote if code == 0 else ""
    code, status = _git(["status", "--porcelain"], path)
    if code == 0 and status:
        state.dirty = True
        state.dirty_files = len(status.splitlines())
    return state


def create_checkout_from(source: Path, target: TargetRepository) -> Path:
    """Copy a repository the operator already has into the lab's own folder.

    This is the one place the lab creates a checkout rather than watching for
    one (ADR-0045). It never writes to ``source``: ``git clone`` reads it, and
    ``--no-hardlinks`` keeps the new object store independent of theirs. The
    copy exists because a run container mounts only the lab root, so a
    repository living anywhere else on the host is invisible to every run.
    """
    if not (source / ".git").exists():
        raise CheckoutError(f"{source} is not a git repository.")
    destination = checkout_path_for(target.target_id)
    if destination.exists():
        raise CheckoutError(
            f"{destination} already exists. Attach it instead, or remove it "
            "first — the lab will not overwrite a checkout."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    code, output = _git(
        ["clone", "--no-hardlinks", str(source), str(destination)],
        destination.parent,
        timeout=600,
    )
    if code != 0:
        raise CheckoutError(f"Could not copy {source}: {output}")
    ref = (target.default_ref or "").strip()
    if ref:
        # Best effort: a clone lands on the source's default branch, which is
        # usually but not always the target's ref. A failure here is not an
        # error — the UI shows the branch and HEAD it actually got, and the pin
        # records the commit the operator sees.
        _git(["checkout", "--quiet", ref], destination)
    if target.repository_url:
        # The clone's origin would otherwise be the mount path the copy came
        # through (/host-sources/...), which names nothing outside this
        # container and stops existing the moment the mount changes. The
        # repository of record is the URL the operator declared — the same
        # origin the guided clone would have produced.
        _git(["remote", "set-url", "origin", target.repository_url], destination)
    return destination


def list_checkouts() -> list[dict[str, object]]:
    """Every clone sitting under ``data/checkouts``, whoever put it there.

    The lab watches one directory per target by convention, so a clone made
    under a different name — a typo, an older target id, a repository cloned
    once and audited twice — is invisible until somebody attaches it. This is
    what the UI offers instead of asking for a second copy.
    """
    root = get_settings().checkouts_dir
    if not root.is_dir():
        return []
    rows: list[dict[str, object]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir() or entry.name.startswith(".") or entry.is_symlink():
            continue
        if not (entry / ".git").exists():
            continue
        code, head = _git(["rev-parse", "HEAD"], entry)
        if code != 0:
            continue
        _, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], entry)
        code, remote = _git(["remote", "get-url", "origin"], entry)
        rows.append(
            {
                "name": entry.name,
                "path": str(entry),
                "head": head,
                "branch": branch,
                "remote_url": remote if code == 0 else "",
            }
        )
    return rows


def resolve_local_ref(target: TargetRepository, ref: str) -> str:
    """Resolve ``ref`` to a commit inside the operator's clone.

    Used instead of ``git ls-remote`` once a checkout exists: it needs no
    network and no credentials, and it resolves a local branch the operator
    created that the remote has never heard of.
    """
    path = resolve_checkout_path(target)
    wanted = (ref or "").strip()
    if not wanted:
        raise CheckoutError("A ref is required (branch, tag or commit SHA).")
    code, output = _git(["rev-parse", "--verify", f"{wanted}^{{commit}}"], path)
    if code != 0:
        raise CheckoutError(f"Could not resolve {wanted!r} in the local checkout.")
    return output.split()[0] if output else ""


def _read_capped(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= limit:
        return text.strip()
    return text[:limit].strip() + "\n…(truncated)"


def _tree(root: Path) -> list[str]:
    """Two levels of directory layout, ignoring build output."""
    entries: list[str] = []
    try:
        top = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return entries
    for item in top:
        if item.name.startswith(".") or item.name in _IGNORED_DIRS:
            continue
        if item.is_dir():
            entries.append(f"  {item.name}/")
            try:
                children = sorted(item.iterdir(), key=lambda child: child.name)
            except OSError:
                children = []
            for child in children:
                if child.name.startswith(".") or child.name in _IGNORED_DIRS:
                    continue
                entries.append(f"    {child.name}{'/' if child.is_dir() else ''}")
                if len(entries) >= _TREE_ENTRIES:
                    return entries
        else:
            entries.append(f"  {item.name}")
        if len(entries) >= _TREE_ENTRIES:
            break
    return entries


def _test_dirs(root: Path) -> list[str]:
    found: list[str] = []
    for name in ("tests", "test", "spec", "__tests__"):
        candidate = root / name
        if candidate.is_dir():
            found.append(f"{name}/")
    for item in sorted(root.iterdir()) if root.is_dir() else []:
        if not item.is_dir() or item.name.startswith(".") or item.name in _IGNORED_DIRS:
            continue
        for name in ("tests", "test"):
            if (item / name).is_dir():
                found.append(f"{item.name}/{name}/")
    return found[:8]


def repo_context(target: TargetRepository) -> RepoContext:
    """Describe the checkout well enough to draft a case against it.

    Read from the working tree rather than from git, because that is what the
    agent will see, and capped hard: the assistant's job is to write a case for
    this repository, not to summarise it.
    """
    context = RepoContext(
        setup_commands=list(target.setup_commands),
        validation_commands=list(target.validation_commands),
    )
    path = resolve_checkout_path(target)
    if not path.is_dir():
        return context
    for name in _README_NAMES:
        candidate = path / name
        if candidate.is_file():
            context.readme = _read_capped(candidate, _README_CHARS)
            break
    for name in _RULES_NAMES:
        candidate = path / name
        if candidate.is_file():
            context.rules_file = name
            context.rules_body = _read_capped(candidate, _RULES_CHARS)
            break
    context.tree = _tree(path)
    context.test_dirs = _test_dirs(path)
    return context
