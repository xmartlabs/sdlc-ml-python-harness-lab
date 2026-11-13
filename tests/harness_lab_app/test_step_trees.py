"""Rebuilding the tree a step of a cloned-repository run left (ADR-0076)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harness_lab_app.step_trees import StepTreeError, step_tree

_IDENT = ["-c", "user.email=t@t", "-c", "user.name=t"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *_IDENT, "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _clone_with_two_steps(tmp_path: Path) -> tuple[Path, str, Path, Path]:
    """A clone at ``base``, step 1 edits a file, step 2 also adds one."""
    clone = tmp_path / "workspace"
    clone.mkdir()
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    (clone / "a.py").write_text("x = 1\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "provisioned")
    base = _git(clone, "rev-parse", "HEAD")

    steps = tmp_path / "steps"
    steps.mkdir()
    (clone / "a.py").write_text("x = 2\n")
    (steps / "step-1.diff").write_text(_git(clone, "diff", "HEAD") + "\n")
    (clone / "new.py").write_text("y = 1\n")
    _git(clone, "add", "-N", "new.py")
    (steps / "step-2.diff").write_text(_git(clone, "diff", "HEAD") + "\n")
    return clone, base, steps / "step-1.diff", steps / "step-2.diff"


def test_a_step_tree_is_the_base_plus_that_steps_diff(tmp_path):
    clone, base, step1, step2 = _clone_with_two_steps(tmp_path)

    with step_tree(clone, base, step1, tmp_path, 1) as tree:
        assert (tree / "a.py").read_text() == "x = 2\n"
        assert not (tree / "new.py").exists()  # added only in step 2
    with step_tree(clone, base, step2, tmp_path, 2) as tree:
        assert (tree / "new.py").read_text() == "y = 1\n"

    # Gone afterwards, and the archived clone is exactly as it was.
    assert not (tmp_path / "step-tree-1").exists()
    assert not (tmp_path / "step-tree-2").exists()
    assert (clone / "a.py").read_text() == "x = 2\n"


def test_a_step_tree_is_a_self_contained_repository(tmp_path):
    """Graded inside another container, where only the tree is mounted.

    A worktree's `.git` *file* pointed at the archived clone by a worker path;
    in the evaluation container `ansible-test` ran `git ls-files`, git died
    with "not a git repository", no test ran, and the step read 0/4. The tree
    must carry its own `.git` directory and borrow no objects from outside.
    """
    clone, base, step1, _ = _clone_with_two_steps(tmp_path)

    with step_tree(clone, base, step1, tmp_path, 1) as tree:
        assert (tree / ".git").is_dir()
        assert not (tree / ".git" / "objects" / "info" / "alternates").exists()
        moved = tmp_path / "elsewhere"
        shutil.copytree(tree, moved, symlinks=True)
    # Still a working repository once the original clone is out of reach.
    shutil.rmtree(clone)
    assert "a.py" in _git(moved, "ls-files")
    assert _git(moved, "rev-parse", "HEAD") == base


def test_an_empty_diff_is_the_base_itself(tmp_path):
    clone, base, _, _ = _clone_with_two_steps(tmp_path)
    empty = tmp_path / "steps" / "step-3.diff"
    empty.write_text("")

    with step_tree(clone, base, empty, tmp_path, 3) as tree:
        assert (tree / "a.py").read_text() == "x = 1\n"


def test_a_diff_that_does_not_apply_is_an_error_not_a_wrong_tree(tmp_path):
    clone, base, _, _ = _clone_with_two_steps(tmp_path)
    broken = tmp_path / "steps" / "step-9.diff"
    broken.write_text("--- a/missing.py\n+++ b/missing.py\n@@ -1 +1 @@\n-a\n+b\n")

    with pytest.raises(StepTreeError), step_tree(clone, base, broken, tmp_path, 9):
        pass
    assert not (tmp_path / "step-tree-9").exists()


def test_a_missing_clone_is_an_error(tmp_path):
    with (
        pytest.raises(StepTreeError),
        step_tree(tmp_path / "nope", "HEAD", tmp_path / "d", tmp_path, 1),
    ):
        pass
