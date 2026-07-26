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
