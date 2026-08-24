from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

# scripts/ is not a package; make bench_run importable the same way it runs.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_run  # noqa: E402


def _args(**over) -> Namespace:
    base = {"prompt": "sdlc", "executor_model": "stub-executor", "planner_model": None}
    base.update(over)
    return Namespace(**base)


def test_resolve_returns_registered_run(tmp_path):
    runs_path = tmp_path / "runs.yaml"
    runs_path.write_text(
        yaml.safe_dump(
            {
                "runs": [
                    {"run_id": "tetris-v1-A-001", "status": "pending", "harness": "x"}
                ]
            }
        )
    )
    run = bench_run.resolve_or_synthesize_run(runs_path, "tetris-v1-A-001", _args())
    assert run["harness"] == "x"


def test_resolve_synthesizes_unregistered_run(tmp_path):
    # A web-lane id (K-NNN) absent from runs.yaml must not exit — it is synthesized.
    runs_path = tmp_path / "runs.yaml"
    runs_path.write_text(yaml.safe_dump({"runs": []}))
    run = bench_run.resolve_or_synthesize_run(runs_path, "tetris-v1-K-007", _args())
    assert run["run_id"] == "tetris-v1-K-007"
    assert run["status"] == "pending"
    assert run["prompt_variant"] == "sdlc"
    assert run["langfuse_session_id"] == "bench-tetris-v1-K-007"


def test_finalize_workspace_archives_and_removes(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    ws = workspace_root / "run-1"
    ws.mkdir(parents=True)
    (ws / "main.py").write_text("print('tetris')\n")
    out_dir = tmp_path / "data" / "runs" / "run-1"
    out_dir.mkdir(parents=True)

    # finalize_workspace cleans WORKSPACE_ROOT when empty — point it at the tmp one.
    monkeypatch.setattr(bench_run, "WORKSPACE_ROOT", workspace_root)

    diff_lines = bench_run.finalize_workspace(out_dir, ws, "run-1")

    assert not ws.exists()  # live workspace removed
    assert (out_dir / "workspace" / "main.py").exists()  # archived copy kept
    assert diff_lines >= 0


def test_finalize_workspace_missing_is_safe(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    missing = tmp_path / "workspace" / "ghost"
    assert bench_run.finalize_workspace(out_dir, missing, "ghost") == 0


def test_finalize_workspace_survives_dangling_symlinks(tmp_path, monkeypatch):
    """Regression: real repos (openlibrary vendoring infogami) contain internal
    and dangling symlinks; following them crashed copytree mid-archive."""
    workspace_root = tmp_path / "workspace"
    ws = workspace_root / "run-sym"
    ws.mkdir(parents=True)
    (ws / "main.py").write_text("print('x')\n")
    (ws / "vendor").mkdir()
    (ws / "vendor" / "real.py").write_text("# vendored\n")
    (ws / "loop-link").symlink_to(ws / "vendor")  # internal link
    (ws / "dangling").symlink_to(ws / "does-not-exist")  # dangling link
    out_dir = tmp_path / "data" / "runs" / "run-sym"
    out_dir.mkdir(parents=True)
    monkeypatch.setattr(bench_run, "WORKSPACE_ROOT", workspace_root)

    diff_lines = bench_run.finalize_workspace(out_dir, ws, "run-sym")

    assert diff_lines >= 0
    assert (out_dir / "workspace" / "main.py").exists()
    # Links are archived as links, not followed.
    assert (out_dir / "workspace" / "loop-link").is_symlink()


def test_workspace_fingerprint_detects_changes(tmp_path):
    import subprocess

    ws = tmp_path / "repo"
    ws.mkdir()
    ident = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    subprocess.run(["git", *ident, "-C", str(ws), "commit", "-qm", "base"], check=True)

    fp1 = bench_run.workspace_fingerprint(ws)
    fp_same = bench_run.workspace_fingerprint(ws)
    assert fp1 is not None and fp1 == fp_same  # no edits → stable
    (ws / "a.py").write_text("x = 2\n")
    assert bench_run.workspace_fingerprint(ws) != fp1  # edit → changes
    # Unreadable git state → None, never raises.
    assert bench_run.workspace_fingerprint(tmp_path / "not-a-repo") is None


def test_validate_workspace_fresh_resets_stale_dir(tmp_path, capsys):
    ws = tmp_path / "run-2"
    ws.mkdir()
    (ws / "stale.txt").write_text("old")
    # fresh=True must reset instead of sys.exit.
    bench_run.validate_workspace(ws, "run-2", resume=False, fresh=True)
    assert not ws.exists()


def test_validate_workspace_exits_without_fresh_or_resume(tmp_path):
    ws = tmp_path / "run-3"
    ws.mkdir()
    (ws / "stale.txt").write_text("old")
    with pytest.raises(SystemExit):
        bench_run.validate_workspace(ws, "run-3", resume=False, fresh=False)


def test_read_template_ref_prefers_installed_manifest(tmp_path):
    (tmp_path / ".harness-version.yaml").write_text(
        "schema_version: 1\ninstalled:\n  repository: x\n  ref: v0.4.0\n"
    )
    (tmp_path / ".template-version").write_text("ref: v0.1.0\ncommit: abc\n")
    assert bench_run.read_template_ref(tmp_path) == "v0.4.0"


def test_read_template_ref_falls_back_to_legacy_then_empty(tmp_path):
    (tmp_path / ".template-version").write_text("ref: 'v0.3.0'\n")
    assert bench_run.read_template_ref(tmp_path) == "v0.3.0"
    assert bench_run.read_template_ref(tmp_path / "nowhere") == ""


# -- workspace anchoring and artifact capture --------------------------------
#
# Runs K-037 and K-039 are the reason these exist. The case prompt shipped a
# literal `{run_id}` placeholder and a *relative* workspace path; the agents
# resolved it to `/workspace/<run>/` at the container root, outside the only
# bind-mounted directory, and 895 and 116 writes respectively went to a
# filesystem that dies with the container. The lab then archived an empty
# directory and recorded both as ordinary completions awaiting a manual score.


def test_the_prompt_names_an_absolute_workspace_inside_the_mount(tmp_path):
    path = bench_run.agent_workspace_path("tetris-v1-K-099")

    assert Path(path).is_absolute()
    assert path.endswith("/workspace/tetris-v1-K-099")


def test_case_prompt_placeholders_are_substituted(tmp_path):
    body = (
        "All files go inside `workspace/{run_id}/`.\n"
        "Run: `python workspace/{run_id}/main.py`\n"
        "# Plan — {run_id}\n"
    )
    rendered = bench_run.render_prompt_body(body, "tetris-v1-K-099")

    # The literal placeholder reaching the model is the bug: it was handed the
    # string "workspace/{run_id}/main.py" and had to invent a real path.
    assert "{run_id}" not in rendered
    assert rendered.count("tetris-v1-K-099") == 3


def test_substitution_leaves_other_braces_alone():
    """Why `replace` and not `str.format`: prompt bodies contain code."""
    body = "Use `{'a': 1}` and f\"{x}\" for {run_id}"
    assert (
        bench_run.render_prompt_body(body, "r-1")
        == "Use `{'a': 1}` and f\"{x}\" for r-1"
    )


def test_generated_prompt_tells_the_agent_where_files_survive(tmp_path, monkeypatch):
    monkeypatch.setattr(bench_run, "WORKSPACE_ROOT", tmp_path / "workspace")
    source = tmp_path / "body.md"
    source.write_text("Build it in `workspace/{run_id}/`.\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    path = bench_run.generate_prompt(
        run_id="tetris-v1-K-099",
        prompt_short="sdlc_full",
        prompt_source=source,
        planner_model=None,
        executor_model="lmstudio/x",
        session_id="s",
        out_dir=out_dir,
    )
    text = path.read_text()

    absolute = str((tmp_path / "workspace" / "tetris-v1-K-099").resolve())
    assert absolute in text
    assert "{run_id}" not in text
    # And it warns off the path the agents actually drifted to.
    assert "/workspace/tetris-v1-K-099/" in text


def test_finalize_workspace_produces_a_real_diff(tmp_path):
    """`git diff --no-index /dev/null <dir>` silently produced nothing.

    git pairs the basename and fails with `Could not access '<dir>/null'`,
    writing an empty patch — and bench_audit omits its "código producido"
    section when the patch is empty, so the auditor never saw the code.
    """
    workspace = tmp_path / "workspace" / "tetris-v1-K-099"
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("import pygame\nprint('tetris')\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    diff_lines = bench_run.finalize_workspace(out_dir, workspace, "tetris-v1-K-099")

    patch = (out_dir / "diff.patch").read_text()
    assert diff_lines > 0
    assert "main.py" in patch
    assert "+import pygame" in patch
    # The archive is what a human scores, so it has to survive the cleanup.
    assert (out_dir / "workspace" / "main.py").exists()


def test_finalize_workspace_reports_an_empty_archive(tmp_path, capsys):
    """K-039: nothing on disk, yet the run was recorded as a completion."""
    workspace = tmp_path / "workspace" / "tetris-v1-K-099"
    workspace.mkdir(parents=True)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    bench_run.finalize_workspace(out_dir, workspace, "tetris-v1-K-099")

    assert "produced no artifact" in capsys.readouterr().out


def test_case_spec_loads_even_without_a_repo(tmp_path):
    """A DB-native case has no subject repo, and that is not a reason to
    discard its spec: the spec is the only description the runner gets."""
    (tmp_path / "case_spec.json").write_text(
        '{"case_type": "multi_step", "case_id": "doom-like", "repo": "", '
        '"prompt": "Build it."}'
    )

    spec = bench_run._load_case_spec(tmp_path)

    assert spec is not None
    assert spec["case_id"] == "doom-like"
    assert bench_run.spec_provisions_repo(spec) is False


def test_case_spec_marks_a_swe_case_as_provisioning(tmp_path):
    (tmp_path / "case_spec.json").write_text(
        '{"case_type": "swe_bench_instance", "repo": "pallets/flask"}'
    )

    assert bench_run.spec_provisions_repo(bench_run._load_case_spec(tmp_path)) is True


def test_a_bare_repo_name_is_not_a_cloneable_slug(tmp_path):
    """Tetris carries ``subject_repo: ml-python-base`` for provenance only."""
    assert bench_run.spec_provisions_repo({"repo": "ml-python-base"}) is False
    assert bench_run.spec_provisions_repo(None) is False


def test_missing_or_corrupt_case_spec_is_absent_not_fatal(tmp_path):
    assert bench_run._load_case_spec(tmp_path) is None
    (tmp_path / "case_spec.json").write_text("{not json")
    assert bench_run._load_case_spec(tmp_path) is None


def test_case_step_prompt_carries_the_author_text_and_the_workspace(tmp_path):
    """A UI-authored step must not be rewritten into the Tetris framing."""
    path = bench_run.generate_case_step_prompt(
        "doom-like-K-001", 2, tmp_path, "Add collision detection."
    )
    text = path.read_text()

    assert path.name == "step-2-prompt.md"
    assert "Add collision detection." in text
    assert bench_run.agent_workspace_path("doom-like-K-001") in text
    assert "ALL MILESTONES COMPLETE" in text
