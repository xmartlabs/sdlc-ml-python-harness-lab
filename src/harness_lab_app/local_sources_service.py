"""Browsing the operator's own repositories, when they opt in (ADR-0045).

The guided flow asks an engineer to type an absolute path they cannot see from
the browser, which is the one step of onboarding a form cannot help with. This
module makes a folder picker possible: the operator bind-mounts one directory of
their own repositories read-only, and the lab lists directories under it.

Two things keep this from becoming "the lab reads your disk". The mount is
read-only and rooted at one directory — the folder the lab itself sits in unless
``HARNESS_LAB_LOCAL_SOURCES_DIR`` points elsewhere — and every path is resolved
and refused unless it lands under that root, symlinks included, which is why
resolution happens before the check and not after.

Nothing here writes. Copying a repository into the lab's own folder is
``checkout_service.create_checkout_from``.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - list form, fixed env, read-only git commands
from pathlib import Path

from harness_lab_app.config import get_settings

# Directories nobody is auditing, and that make a picker unreadable.
_HIDDEN = {
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
}

# A listing is for choosing a repository, not for surveying a filesystem. A
# directory with more children than this is almost certainly not the one.
_MAX_ENTRIES = 200


def _stat_says_directory(path: Path) -> bool:
    """Is this a directory, on a real home directory rather than a fixture?

    A home directory holds unix sockets, dead symlinks and folders the container
    is not allowed to stat. ``Path.is_dir`` raises ``OSError`` on all three, and
    one of them anywhere in the tree would otherwise turn the whole picker into
    a 500. Anything that cannot be answered is not something to offer.
    """
    try:
        return path.is_dir()
    except OSError:
        return False


def _looks_like_repo(path: Path) -> bool:
    """``.git`` present, as a directory or as the file a worktree leaves."""
    try:
        return (path / ".git").exists()
    except OSError:
        return False


# Read-only git, no network, no credential prompt. The mount is read-only, so
# anything that would touch the operator's repository fails rather than writes.
_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
    "GIT_ASKPASS": "",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def _git_line(path: Path, args: list[str]) -> str:
    """One line of git output, or "" for anything that did not work.

    Everything read here is a convenience — a URL and a branch to fill a form
    with — so a repository that cannot answer is not an error, just a form the
    operator finishes themselves.
    """
    try:
        result = subprocess.run(  # nosec B603 - list form, fixed env
            ["git", *args],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=10,
            env=_GIT_ENV,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


class LocalSourcesError(ValueError):
    """A local source directory could not be listed as asked."""


class LocalSourcesDisabled(LocalSourcesError):
    """No local sources root is configured, so there is nothing to browse."""


def is_enabled() -> bool:
    """Whether the operator mounted a directory of their own repositories."""
    settings = get_settings()
    return bool(settings.local_sources_root and settings.host_local_sources_root)


def _root() -> Path:
    if not is_enabled():
        raise LocalSourcesDisabled(
            "No local sources directory is configured. Set "
            "HARNESS_LAB_LOCAL_SOURCES_DIR in .env and restart the lab to browse "
            "your own repositories from here."
        )
    root = Path(get_settings().local_sources_root)
    if not root.is_dir():
        raise LocalSourcesError(
            f"The configured local sources directory ({root}) is not mounted. "
            "Restart the lab after setting HARNESS_LAB_LOCAL_SOURCES_DIR."
        )
    return root.resolve()


def resolve(relative: str) -> Path:
    """Turn a browser-supplied relative path into a directory under the root.

    Resolved first, checked second: a symlink pointing out of the mount would
    otherwise pass a textual check and read anything the container can see.
    """
    root = _root()
    cleaned = (relative or "").strip().strip("/")
    candidate = (root / cleaned).resolve() if cleaned else root
    if candidate != root and root not in candidate.parents:
        raise LocalSourcesError(f"{relative!r} is outside the local sources root.")
    if not _stat_says_directory(candidate):
        raise LocalSourcesError(f"{relative!r} is not a directory.")
    return candidate


def _host_root() -> Path:
    """The root as the operator's shell names it, without the ``..`` in it.

    The default is expressed relative to the lab's own directory, so the raw
    value reads ``/somewhere/the-lab/..``. Nobody types that into a terminal.
    """
    return Path(os.path.normpath(get_settings().host_local_sources_root))


def host_view_of(path: Path) -> str:
    """The same directory as the operator's own shell sees it."""
    root = _root()
    try:
        return str(_host_root() / path.relative_to(root))
    except ValueError:
        return str(path)


def browse(relative: str = "") -> dict[str, object]:
    """One level of directories under the root, marking which are git repos."""
    root = _root()
    here = resolve(relative)
    try:
        children = sorted(here.iterdir(), key=lambda entry: entry.name.lower())
    except OSError as exc:
        # A directory the container may see but not read. Saying so beats a 500
        # from a picker the operator was told to browse freely.
        raise LocalSourcesError(
            f"{relative!r} cannot be listed: {exc.strerror}."
        ) from exc
    entries: list[dict[str, object]] = []
    for item in children:
        if item.name.startswith(".") or item.name in _HIDDEN:
            continue
        if not _stat_says_directory(item):
            continue
        entries.append(
            {
                "name": item.name,
                "path": str(item.relative_to(root)),
                "is_repo": _looks_like_repo(item),
            }
        )
        if len(entries) >= _MAX_ENTRIES:
            break
    here_is_repo = _looks_like_repo(here)
    current = "" if here == root else str(here.relative_to(root))
    parent: str | None = None
    if here != root:
        above = Path(current).parent
        parent = "" if above == Path(".") else str(above)
    return {
        "enabled": True,
        "root": str(_host_root()),
        "path": current,
        "host_path": host_view_of(here),
        "parent": parent,
        "is_repo": here_is_repo,
        # What the registration form asks for, read off the folder itself: a
        # repository the operator already has knows its own remote and branch,
        # and asking them to retype either is the friction the picker exists to
        # remove.
        "remote_url": (
            _git_line(here, ["config", "--get", "remote.origin.url"])
            if here_is_repo
            else ""
        ),
        # `symbolic-ref`, not `rev-parse --abbrev-ref`: the latter answers
        # "HEAD" on a detached head and works on nothing at all before the first
        # commit. A branch name or nothing is the useful pair here.
        "branch": (
            _git_line(here, ["symbolic-ref", "--short", "HEAD"]) if here_is_repo else ""
        ),
        "entries": entries,
    }
