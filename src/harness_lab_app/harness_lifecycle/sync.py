"""Governance sync preview: allowlists and read-only diff classification.

Phase 4 (this module, read-only slice) resolves the governance path allowlist and
classifies the incoming changes of a candidate release into governance (safe),
forbidden/platform (blockers), and other. It performs NO working-tree mutation —
the actual worktree sync lands in a later slice. Forbidden paths must stay
byte-identical under a governance-channel sync (plan §11.1 / docs/template-sync.md).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_lab_app.harness_lifecycle.releases import run_git

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Governance layer safe to project from an immutable template release.
DEFAULT_GOVERNANCE_PATHS: tuple[str, ...] = (
    ".github/skills",
    ".github/skills-external",
    ".github/agents",
    ".github/architecture.md",
    ".github/standards.md",
    ".github/domain-boundaries.md",
    ".github/sdlc.md",
    ".github/orchestration.md",
    ".github/automation.md",
    ".github/portability.md",
    "adapters/templates",
)

# Paths a governance sync must NEVER change (kept byte-identical).
FORBIDDEN_PATHS: tuple[str, ...] = (
    "src",
    "tests",
    "benchmarks",
    "data",
    "memory",
    "Makefile",
    "scripts",
    "adapters/registry.toml",
)


@dataclass(frozen=True)
class SyncPreview:
    governance: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    other: tuple[str, ...] = ()

    @property
    def has_blockers(self) -> bool:
        return bool(self.forbidden)


def governance_paths(root: Path) -> list[str]:
    """Allowlist from ``adapters/registry.toml [template_sync]`` else the default."""
    registry = root / "adapters" / "registry.toml"
    if registry.is_file():
        data = tomllib.loads(registry.read_text(encoding="utf-8"))
        paths = data.get("template_sync", {}).get("governance_paths")
        if paths:
            return list(paths)
    return list(DEFAULT_GOVERNANCE_PATHS)


def _matches(path: str, prefixes: tuple[str, ...] | list[str]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def is_forbidden(path: str, forbidden: tuple[str, ...] = FORBIDDEN_PATHS) -> bool:
    return _matches(path, forbidden)


def classify_preview(
    entries: list[tuple[str, str]],
    governance: list[str],
    forbidden: tuple[str, ...] = FORBIDDEN_PATHS,
) -> SyncPreview:
    """Bucket (status, path) changes into governance / forbidden / other.

    A forbidden-path hit is a blocker: a governance-channel sync must not touch it.
    """
    gov: list[str] = []
    forb: list[str] = []
    other: list[str] = []
    for _status, path in entries:
        if _matches(path, governance):
            gov.append(path)
        elif is_forbidden(path, forbidden):
            forb.append(path)
        else:
            other.append(path)
    return SyncPreview(governance=tuple(gov), forbidden=tuple(forb), other=tuple(other))


def changed_paths_between(
    root: Path,
    base: str,
    ref: str,
    paths: list[str] | None = None,
    runner: Callable = run_git,
) -> list[tuple[str, str]]:
    """Read-only ``git diff --name-status base..ref`` as (status, path) tuples.

    When ``paths`` is given the diff is limited to them — a governance sync only
    ever changes the governance allowlist, so callers scope the diff to it rather
    than comparing whole (often unrelated) trees.
    """
    cmd = ["diff", "--name-status", f"{base}..{ref}"]
    if paths:
        cmd += ["--", *paths]
    out = runner(cmd, root, check=False).stdout
    entries: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        entries.append((parts[0][0], parts[-1]))
    return entries


# --------------------------------------------------------------------------- #
# Candidate worktree preparation (mutating, but isolated from main)
# --------------------------------------------------------------------------- #

WORKTREES_DIR = ".worktrees"


def candidate_branch(ref: str) -> str:
    return f"harness-candidate/{ref}"


def candidate_worktree_path(root: Path, ref: str) -> Path:
    return root / WORKTREES_DIR / f"harness-candidate-{ref}"


def main_tree_clean(root: Path, runner: Callable = run_git) -> bool:
    return not runner(["status", "--porcelain"], root, check=False).stdout.strip()


def create_candidate_worktree(root: Path, ref: str, runner: Callable = run_git) -> Path:
    """Create an isolated worktree + branch off HEAD; main is never touched."""
    path = candidate_worktree_path(root, ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    runner(["worktree", "add", "-b", candidate_branch(ref), str(path), "HEAD"], root)
    return path


def _paths_present_at_ref(
    root: Path, ref: str, paths: list[str], runner: Callable
) -> list[str]:
    present = []
    for p in paths:
        if runner(["cat-file", "-e", f"{ref}:{p}"], root, check=False).returncode == 0:
            present.append(p)
    return present


def apply_governance_sync(
    worktree: Path, ref: str, governance: list[str], runner: Callable = run_git
) -> list[str]:
    """Check out governance paths from ``ref`` into the worktree (index + tree).

    Runs inside the isolated worktree only. Returns the paths actually applied.
    """
    applied = _paths_present_at_ref(worktree, ref, governance, runner)
    for p in applied:
        runner(["checkout", ref, "--", p], worktree)
    return applied


def worktree_changed_paths(
    worktree: Path, runner: Callable = run_git
) -> list[tuple[str, str]]:
    """(status, path) of everything changed in the worktree vs its HEAD."""
    out = runner(["status", "--porcelain"], worktree, check=False).stdout
    entries: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        status = line[:2].strip() or "?"
        entries.append((status[0], line[3:].strip()))
    return entries


def forbidden_changes(
    entries: list[tuple[str, str]], forbidden: tuple[str, ...] = FORBIDDEN_PATHS
) -> list[str]:
    """Paths in ``entries`` that a governance sync must never have touched."""
    return [p for _status, p in entries if is_forbidden(p, forbidden)]


def remove_candidate_worktree(root: Path, ref: str, runner: Callable = run_git) -> None:
    """Tear down the candidate worktree and its branch (best-effort cleanup)."""
    path = candidate_worktree_path(root, ref)
    runner(["worktree", "remove", "--force", str(path)], root, check=False)
    runner(["branch", "-D", candidate_branch(ref)], root, check=False)
