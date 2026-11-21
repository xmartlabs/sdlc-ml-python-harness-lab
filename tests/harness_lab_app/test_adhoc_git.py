"""The clean patch and the apply/undo rules, on real repositories (ADR-0095).

Every test builds an actual git repository: the failure modes here are git's
(a hunk that no longer fits, a binary file, a dirty index), and a mocked git
would agree with whatever the code assumed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness_lab_app import adhoc_git
from harness_lab_app.adhoc_git import (
    apply_patch,
    build_change_patch,
    check_apply,
    summarize_patch,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(path: Path) -> tuple[Path, str]:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "app.py").write_text("def greet():\n    return 'hi'\n")
    (path / "README.md").write_text("# demo\n")
    (path / ".gitignore").write_text("ignored/\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path, _git(path, "rev-parse", "HEAD")


def _agent_copy(source: Path, dest: Path) -> Path:
    """What the runner does: clone, then a provisioning commit on top."""
    subprocess.run(
        ["git", "clone", "-q", str(source), str(dest)], check=True, capture_output=True
    )
    (dest / "setup-output.txt").write_text("installed\n")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "target: provisioned demo")
    return dest


@pytest.fixture
def operator(tmp_path: Path) -> tuple[Path, str]:
    return _repo(tmp_path / "operator")


def _agent_change(operator: tuple[Path, str], tmp_path: Path) -> tuple[str, Path]:
    source, base = operator
    copy = _agent_copy(source, tmp_path / "copy")
    (copy / "app.py").write_text("def greet(name):\n    return f'hi {name}'\n")
    (copy / "new_module.py").write_text("VALUE = 1\n")
    (copy / "node_modules").mkdir()
    (copy / "node_modules" / "dep.js").write_text("x\n")
    (copy / "ignored").mkdir()
    (copy / "ignored" / "out.txt").write_text("build\n")
    return build_change_patch(copy, base), copy


def test_patch_carries_the_agent_change_and_nothing_the_lab_put_there(
    operator, tmp_path
):
    patch, _ = _agent_change(operator, tmp_path)

    files = {item.path: item for item in summarize_patch(patch)}
    assert set(files) == {"app.py", "new_module.py"}
    assert files["new_module.py"].status == "added"
    assert files["app.py"].status == "modified"


def test_setup_output_in_the_provisioning_commit_stays_out(operator, tmp_path):
    """Setup output and a projected harness live in the provisioning commit.

    Diffing from the operator's commit would ship them into their repository as
    if the agent had written them; diffing from the provisioning commit keeps
    only what the agent did.
    """
    patch, _ = _agent_change(operator, tmp_path)

    assert "setup-output.txt" not in patch
    assert "node_modules" not in patch
    assert "ignored/out.txt" not in patch


def test_an_operator_commit_saying_provisioned_is_not_mistaken_for_the_lab(
    tmp_path,
):
    source, _ = _repo(tmp_path / "operator")
    (source / "app.py").write_text("v2\n")
    _git(source, "commit", "-qam", "target: provisioned servers")
    base = _git(source, "rev-parse", "HEAD")
    copy = _agent_copy(source, tmp_path / "copy")
    (copy / "app.py").write_text("v3\n")

    patch = build_change_patch(copy, base)

    assert "+v3" in patch
    assert "setup-output.txt" not in patch


def test_a_committed_agent_change_still_appears(operator, tmp_path):
    source, base = operator
    copy = _agent_copy(source, tmp_path / "copy")
    (copy / "app.py").write_text("changed\n")
    _git(copy, "commit", "-qam", "agent committed its own work")

    patch = build_change_patch(copy, base)

    assert "app.py" in {item.path for item in summarize_patch(patch)}


def test_binary_files_survive_the_round_trip(operator, tmp_path):
    source, base = operator
    copy = _agent_copy(source, tmp_path / "copy")
    (copy / "logo.png").write_bytes(bytes(range(256)) * 4)

    patch = build_change_patch(copy, base)
    result = apply_patch(source, patch, base)

    assert result.can_apply, result.blockers
    assert (source / "logo.png").read_bytes() == bytes(range(256)) * 4
    assert any(item.binary for item in summarize_patch(patch))


def test_apply_writes_files_only_and_undo_restores_them(operator, tmp_path):
    source, base = operator
    patch, _ = _agent_change(operator, tmp_path)

    applied = apply_patch(source, patch, base)

    assert applied.can_apply, applied.blockers
    assert "name" in (source / "app.py").read_text()
    assert (source / "new_module.py").exists()
    # Nothing staged, nothing committed: the operator owns those steps.
    assert _git(source, "diff", "--cached", "--name-only") == ""
    assert _git(source, "rev-parse", "HEAD") == base

    undone = apply_patch(source, patch, base, reverse=True)

    assert undone.can_apply, undone.blockers
    assert (source / "app.py").read_text() == "def greet():\n    return 'hi'\n"
    assert not (source / "new_module.py").exists()


def test_uncommitted_edit_in_a_touched_file_blocks_and_writes_nothing(
    operator, tmp_path
):
    source, base = operator
    patch, _ = _agent_change(operator, tmp_path)
    (source / "app.py").write_text("my own work in progress\n")

    result = apply_patch(source, patch, base)

    assert not result.can_apply
    assert result.overlapping_dirty == ["app.py"]
    assert (source / "app.py").read_text() == "my own work in progress\n"
    assert not (source / "new_module.py").exists()


def test_uncommitted_edit_elsewhere_is_a_warning_not_a_blocker(operator, tmp_path):
    source, base = operator
    patch, _ = _agent_change(operator, tmp_path)
    (source / "README.md").write_text("# my notes\n")

    result = apply_patch(source, patch, base)

    assert result.can_apply, result.blockers
    assert any("uncommitted" in warning for warning in result.warnings)
    assert (source / "README.md").read_text() == "# my notes\n"


def test_a_moved_head_that_still_fits_applies_with_a_warning(operator, tmp_path):
    source, base = operator
    patch, _ = _agent_change(operator, tmp_path)
    (source / "README.md").write_text("# demo\n\nmore\n")
    _git(source, "commit", "-qam", "operator kept working")

    result = apply_patch(source, patch, base)

    assert result.can_apply, result.blockers
    assert result.head_moved_by == 1


def test_a_conflicting_commit_refuses_the_whole_patch(operator, tmp_path):
    source, base = operator
    patch, _ = _agent_change(operator, tmp_path)
    (source / "app.py").write_text("def greet():\n    return 'hello there'\n")
    _git(source, "commit", "-qam", "operator rewrote the same line")

    result = apply_patch(source, patch, base)

    assert not result.can_apply
    assert any("does not fit" in blocker for blocker in result.blockers)
    # All or nothing: the new file was not created either.
    assert not (source / "new_module.py").exists()


def test_undo_is_refused_once_the_applied_files_changed(operator, tmp_path):
    source, base = operator
    patch, _ = _agent_change(operator, tmp_path)
    assert apply_patch(source, patch, base).can_apply
    (source / "app.py").write_text("edited after apply\n")

    result = apply_patch(source, patch, base, reverse=True)

    assert not result.can_apply
    assert (source / "app.py").read_text() == "edited after apply\n"


def test_a_merge_in_progress_blocks(operator, tmp_path):
    source, base = operator
    patch, _ = _agent_change(operator, tmp_path)
    git_dir = source / ".git"
    (git_dir / "MERGE_HEAD").write_text(base + "\n")

    result = check_apply(source, patch, base)

    assert not result.can_apply
    assert any("merge" in blocker for blocker in result.blockers)


def test_an_empty_patch_is_refused(operator):
    source, base = operator

    result = check_apply(source, "", base)

    assert not result.can_apply


def test_flags_mark_what_deserves_a_second_look():
    patch = (
        "diff --git a/uv.lock b/uv.lock\n--- a/uv.lock\n+++ b/uv.lock\n@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
        "--- a/.github/workflows/ci.yml\n+++ b/.github/workflows/ci.yml\n@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/.env b/.env\nnew file mode 100644\n--- /dev/null\n+++ b/.env\n@@ -0,0 +1 @@\n+K=v\n"
        "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
    )
    flags = {item.path: item.flags for item in summarize_patch(patch)}

    assert flags["uv.lock"] == ["lockfile"]
    assert flags[".github/workflows/ci.yml"] == ["ci"]
    assert "secret_like" in flags[".env"]
    assert "deleted" in flags["old.py"]


def test_cli_prints_the_check_as_json_and_exits_by_verdict(operator, tmp_path, capsys):
    source, base = operator
    patch, _ = _agent_change(operator, tmp_path)
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(patch)

    code = adhoc_git.main(["apply", str(source), str(patch_file), base])

    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["can_apply"] is True
    assert (source / "new_module.py").exists()
