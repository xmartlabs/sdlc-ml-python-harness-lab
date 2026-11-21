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


def _agent_change(operator: tuple[Path, str], tmp_path: Path) -> tuple[bytes, Path]:
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

    assert b"setup-output.txt" not in patch
    assert b"node_modules" not in patch
    assert b"ignored/out.txt" not in patch


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

    assert b"+v3" in patch
    assert b"setup-output.txt" not in patch


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

    result = check_apply(source, b"", base)

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
    patch_file.write_bytes(patch)

    code = adhoc_git.main(["apply", str(source), str(patch_file), base])

    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["can_apply"] is True
    assert (source / "new_module.py").exists()


# ---------------------------------------------------------------------------
# Regressions from the 2026-11-21 review: real repositories are not ASCII,
# not LF, and an agent's .git is not to be trusted.
# ---------------------------------------------------------------------------


def test_a_crlf_file_round_trips_byte_for_byte(tmp_path):
    source, _ = _repo(tmp_path / "operator")
    (source / "run.bat").write_bytes(b"@echo off\r\necho one\r\n")
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "windows file")
    base = _git(source, "rev-parse", "HEAD")
    copy = _agent_copy(source, tmp_path / "copy")
    (copy / "run.bat").write_bytes(b"@echo off\r\necho two\r\n")

    patch = build_change_patch(copy, base)
    result = apply_patch(source, patch, base)

    assert b"\r\n" in patch
    assert result.can_apply, result.blockers
    assert (source / "run.bat").read_bytes() == b"@echo off\r\necho two\r\n"


def test_a_latin1_file_does_not_break_the_patch(tmp_path):
    source, _ = _repo(tmp_path / "operator")
    (source / "legacy.txt").write_bytes("caf\xe9\n".encode("latin-1"))
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "latin-1")
    base = _git(source, "rev-parse", "HEAD")
    copy = _agent_copy(source, tmp_path / "copy")
    (copy / "legacy.txt").write_bytes("caf\xe9 cr\xe8me\n".encode("latin-1"))

    patch = build_change_patch(copy, base)
    result = apply_patch(source, patch, base)

    assert result.can_apply, result.blockers
    assert (source / "legacy.txt").read_bytes() == "caf\xe9 cr\xe8me\n".encode(
        "latin-1"
    )


def test_an_agent_config_cannot_run_commands_in_the_lab(tmp_path):
    source, base = _repo(tmp_path / "operator")
    copy = _agent_copy(source, tmp_path / "copy")
    marker_fsmonitor = tmp_path / "fsmonitor-ran"
    marker_filter = tmp_path / "filter-ran"
    marker_diff = tmp_path / "extdiff-ran"
    with (copy / ".git" / "config").open("a") as config:
        config.write(
            f'[core]\n\tfsmonitor = "touch {marker_fsmonitor}; false"\n'
            f'[filter "evil"]\n\tclean = "touch {marker_filter}; cat"\n'
            f'[diff]\n\texternal = "touch {marker_diff}"\n'
        )
    (copy / ".gitattributes").write_text("*.py filter=evil\n")
    (copy / "app.py").write_text("changed\n")

    patch = build_change_patch(copy, base)
    summarize_patch(patch, copy)

    assert not marker_fsmonitor.exists()
    assert not marker_filter.exists()
    assert not marker_diff.exists()
    assert b"+changed" in patch


def test_an_agent_config_include_is_refused(tmp_path):
    source, base = _repo(tmp_path / "operator")
    copy = _agent_copy(source, tmp_path / "copy")
    with (copy / ".git" / "config").open("a") as config:
        config.write(f"[include]\n\tpath = {tmp_path}/other.cfg\n")

    with pytest.raises(adhoc_git.AdhocGitError, match="includes"):
        build_change_patch(copy, base)


def test_the_recorded_provisioning_sha_survives_an_amend(tmp_path):
    source, base = _repo(tmp_path / "operator")
    copy = _agent_copy(source, tmp_path / "copy")
    provisioned = _git(copy, "rev-parse", "HEAD")
    (copy / "app.py").write_text("agent work\n")
    _git(copy, "commit", "-qa", "--amend", "--no-edit")

    by_grep = build_change_patch(copy, base)
    by_record = build_change_patch(copy, base, provisioned)

    # The amended commit still says "provisioned": the grep finds it and loses
    # the change. The SHA recorded before the agent started does not.
    assert b"agent work" not in by_grep
    assert b"+agent work" in by_record
    assert b"setup-output.txt" not in by_record


def test_non_ascii_and_spaced_paths_are_counted_and_checked(tmp_path):
    source, _ = _repo(tmp_path / "operator")
    (source / "guía de uso.md").write_text("uno\n")
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "doc")
    base = _git(source, "rev-parse", "HEAD")
    copy = _agent_copy(source, tmp_path / "copy")
    (copy / "guía de uso.md").write_text("dos\n")
    patch = build_change_patch(copy, base)

    files = summarize_patch(patch, copy)
    assert [item.path for item in files] == ["guía de uso.md"]
    assert files[0].added == 1 and files[0].removed == 1

    (source / "guía de uso.md").write_text("mi trabajo\n")
    result = check_apply(source, patch, base)
    assert result.overlapping_dirty == ["guía de uso.md"]
    assert not result.can_apply


def test_undo_refuses_when_an_applied_file_was_edited_elsewhere(tmp_path):
    source, _ = _repo(tmp_path / "operator")
    (source / "long.py").write_text("".join(f"line {n}\n" for n in range(40)))
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "long file")
    base = _git(source, "rev-parse", "HEAD")
    copy = _agent_copy(source, tmp_path / "copy")
    (copy / "long.py").write_text(
        "".join(("AGENT\n" if n == 2 else f"line {n}\n") for n in range(40))
    )
    patch = build_change_patch(copy, base)
    applied = apply_patch(source, patch, base)
    assert applied.can_apply and applied.applied_hashes
    # An edit far from the agent's hunk: `git apply -R --check` alone would pass.
    text = (source / "long.py").read_text().replace("line 35\n", "MINE\n")
    (source / "long.py").write_text(text)

    result = apply_patch(
        source, patch, base, reverse=True, expected_hashes=applied.applied_hashes
    )

    assert not result.can_apply
    assert "MINE" in (source / "long.py").read_text()


def test_a_redelivered_apply_reports_the_change_as_already_there(
    operator, tmp_path, capsys
):
    source, base = operator
    patch, _ = _agent_change(operator, tmp_path)
    patch_file = tmp_path / "change.patch"
    patch_file.write_bytes(patch)
    assert adhoc_git.main(["apply", str(source), str(patch_file), base]) == 0
    capsys.readouterr()

    code = adhoc_git.main(["apply", str(source), str(patch_file), base])

    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["warnings"] == ["The change was already applied to this folder."]


def test_build_cli_writes_the_patch_bytes(operator, tmp_path, capsys):
    source, base = operator
    copy = _agent_copy(source, tmp_path / "copy")
    (copy / "app.py").write_text("changed\n")
    out_file = tmp_path / "out.patch"

    code = adhoc_git.main(["build", str(copy), base, str(out_file)])

    assert code == 0
    assert b"+changed" in out_file.read_bytes()


def test_the_summary_survives_being_asked_from_inside_another_repository(
    operator, tmp_path
):
    """The lab reads patches from data/runs/<id>/, a folder inside its own repo.

    `git apply --numstat` run there treats every path as relative to that
    subfolder and drops them all; the first real run showed files_changed=None.
    """
    patch, _ = _agent_change(operator, tmp_path)
    host = tmp_path / "lab"
    host.mkdir()
    _git(host, "init", "-q")
    inside = host / "data" / "runs" / "adhoc-x-001-K-001"
    inside.mkdir(parents=True)

    files = summarize_patch(patch, inside)

    assert {item.path for item in files} == {"app.py", "new_module.py"}
