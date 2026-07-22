"""Template release resolution: strict refs, full-SHA resolution, protocol reads.

Every adopted input is an immutable ``vMAJOR.MINOR.PATCH`` tag resolved to a full
SHA — never mutable ``main`` or a bare branch (plan invariants). Git access goes
through an injectable ``runner`` so the logic is testable against temp repos. All
commands are read-only.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from typing import TYPE_CHECKING

from harness_lab_app.harness_lifecycle.errors import (
    EXIT_INVALID,
    EXIT_MISSING,
    LifecycleError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

RELEASE_REF_RE = re.compile(r"^v\d+\.\d+\.\d+$")
DEFAULT_REMOTE = "template"


def run_git(
    args: list[str], root: Path, *, check: bool = True, capture: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, check=check, capture_output=capture
    )


def is_release_ref(ref: str) -> bool:
    return bool(RELEASE_REF_RE.match(ref))


def validate_release_ref(ref: str) -> None:
    """Raise unless ``ref`` is an immutable ``vX.Y.Z`` tag (never main/branch)."""
    if not is_release_ref(ref):
        raise LifecycleError(
            f"'{ref}' is not a release tag. Use an immutable vMAJOR.MINOR.PATCH tag "
            "(adoption never accepts 'main', a branch, or an unresolved ref).",
            EXIT_INVALID,
        )


def resolve_ref_commit(root: Path, ref: str, runner: Callable = run_git) -> str:
    """Resolve ``ref`` to a full 40-char commit SHA, or raise EXIT_MISSING."""
    result = runner(["rev-parse", "--verify", f"{ref}^{{commit}}"], root, check=False)
    sha = result.stdout.strip()
    if result.returncode != 0 or len(sha) != 40:
        raise LifecycleError(
            f"Cannot resolve '{ref}' to a commit. Is the tag fetched?", EXIT_MISSING
        )
    return sha


def list_release_tags(root: Path, runner: Callable = run_git) -> list[str]:
    out = runner(["tag", "--list", "v*", "--sort=-v:refname"], root, check=False).stdout
    return [t for t in out.split() if is_release_ref(t)]


def latest_release_tag(root: Path, runner: Callable = run_git) -> str | None:
    tags = list_release_tags(root, runner)
    return tags[0] if tags else None


def template_remote_configured(
    root: Path, remote: str = DEFAULT_REMOTE, runner: Callable = run_git
) -> bool:
    return runner(["remote", "get-url", remote], root, check=False).returncode == 0


def fetch_template(
    root: Path, remote: str = DEFAULT_REMOTE, runner: Callable = run_git
) -> None:
    """Fetch tags from the template remote (read-only; updates remote refs only)."""
    runner(["fetch", remote, "--tags"], root, check=False)


def read_sync_protocol_at_ref(
    root: Path, ref: str, runner: Callable = run_git
) -> int | None:
    """Read ``[template_sync].protocol`` from ``adapters/registry.toml`` at ``ref``."""
    result = runner(["show", f"{ref}:adapters/registry.toml"], root, check=False)
    if result.returncode != 0:
        return None
    data = tomllib.loads(result.stdout)
    section = data.get("template_sync", {})
    protocol = section.get("protocol")
    return int(protocol) if protocol is not None else None
