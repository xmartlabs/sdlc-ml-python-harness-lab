"""Rebuild the repository tree one step of a run left behind (ADR-0076).

A cloned repository is recorded per step as a diff (``steps/step-N.diff``)
against the commit that was HEAD when the step ended, because copying the
clone after every step would cost more than the run. Grading such a step means
rebuilding its tree: clone the archived repository, check that commit out and
apply the step's diff on top.

A **self-contained local clone**, not a ``git worktree`` and not
``--shared``: the tree is graded inside another container, where only the tree
itself is mounted. A worktree's ``.git`` is a file pointing back at the archived
clone by its path on this machine, and ``--shared`` points at it through
``objects/info/alternates`` — neither path exists in that container, so any
``git`` call there dies ("not a git repository") and suites that list their
files through git (``ansible-test`` does) never run. ``--local`` hardlinks the
objects instead, so the clone is complete on its own and still costs a
checkout, not a second copy of the history.

The tree is created under ``data/runs/<id>/`` because a docker daemon that is
the host's (the worker's case) can only bind-mount paths under the repository.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 — fixed git argv, no shell
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

# A stray tree (a worker killed mid-grade) is replaced on the next rebuild.
_TREE_PREFIX = "step-tree-"


class StepTreeError(RuntimeError):
    """The step's tree could not be rebuilt; the step stays unmeasured."""


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(  # nosec B603 B607 — fixed git argv, no shell
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise StepTreeError(
            f"git {args[0]} failed: {result.stderr.decode(errors='replace')[-300:]}"
        )
    return result.stdout


@contextmanager
def step_tree(
    clone: Path, base: str, diff: Path, scratch: Path, step: int
) -> Iterator[Path]:
    """Yield a checkout of ``base`` with ``diff`` applied; removed on exit.

    ``clone`` is the run's archived repository, ``scratch`` the run's own
    directory. Raises :class:`StepTreeError` when the base commit is missing
    or the diff does not apply — the step is then reported unmeasured, never
    graded against a tree it did not leave.
    """
    if not (clone / ".git").exists():
        raise StepTreeError(f"no git clone at {clone}")
    tree = (scratch / f"{_TREE_PREFIX}{step}").resolve()
    shutil.rmtree(tree, ignore_errors=True)
    try:
        _git(
            scratch,
            "clone",
            "--quiet",
            "--local",
            "--no-checkout",
            str(clone.resolve()),
            str(tree),
        )
        _git(tree, "checkout", "--quiet", "--detach", base)
        patch = diff.read_bytes()
        if patch.strip():
            _git(tree, "apply", "--binary", "--whitespace=nowarn", "-", stdin=patch)
        yield tree
    finally:
        shutil.rmtree(tree, ignore_errors=True)
