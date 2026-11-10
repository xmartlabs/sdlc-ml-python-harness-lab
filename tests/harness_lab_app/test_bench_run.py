from __future__ import annotations

import json
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


def test_run_models_lists_each_model_once_in_a_stable_order():
    args = Namespace(executor_model="gateway/a", planner_model="gateway/b")
    assert bench_run.run_models(args) == ("gateway/a", "gateway/b")
    same = Namespace(executor_model="lmstudio/x", planner_model="lmstudio/x")
    assert bench_run.run_models(same) == ("lmstudio/x",)
    assert (
        bench_run.run_models(Namespace(executor_model=None, planner_model=None)) == ()
    )


def test_detected_windows_are_off_under_the_probe_kill_switch(monkeypatch):
    """The autouse fixture sets HARNESS_LAB_DISABLE_GATEWAY_PROBE; no host is asked."""
    from harness_lab_app import context_window

    monkeypatch.setattr(
        context_window,
        "detect_windows",
        lambda *_a, **_k: pytest.fail("a host was probed under the kill switch"),
    )
    bench_run.detected_context_windows.cache_clear()
    assert bench_run.detected_context_windows(("gateway/lmstudio-35b",)) == ()


def test_detected_windows_reach_the_overlay_as_limits(tmp_path, monkeypatch):
    from harness_lab_app import context_window
    from harness_lab_app.context_window import ContextWindow

    (tmp_path / "opencode.json").write_text(
        json.dumps(
            {
                "provider": {
                    "gateway": {
                        "npm": "x",
                        "models": {"lmstudio-35b": {"name": "35B"}},
                    }
                }
            }
        )
    )
    monkeypatch.setattr(bench_run, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    window = ContextWindow(
        "gateway/lmstudio-35b",
        "lmstudio",
        "http://gpu:1234",
        "q/35b",
        41216,
        37120,
        4096,
    )
    monkeypatch.setattr(context_window, "detect_windows", lambda *_a, **_k: [window])
    bench_run.detected_context_windows.cache_clear()
    bench_run.apply_gateway_overlay.cache_clear()

    digest = bench_run.apply_gateway_overlay((), ("gateway/lmstudio-35b",))

    assert digest is not None
    effective = json.loads(
        (tmp_path / "data" / "app" / "opencode-effective.json").read_text()
    )
    assert effective["provider"]["gateway"]["models"]["lmstudio-35b"]["limit"] == {
        "context": 37120,
        "output": 4096,
    }
    bench_run.detected_context_windows.cache_clear()
    bench_run.apply_gateway_overlay.cache_clear()


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


def test_merge_usage_never_overwrites_a_value_with_an_empty_one():
    """A remote source that knows the session but has no cost (an unpriced
    Langfuse trace) returns `cost_usd: None`. A plain dict.update let that None
    erase the cost the claude stream-json events had reported, which is how 37
    archived claude runs lost their cost."""
    events = {"cost_usd": 0.42, "input_tokens": 1200, "turns": 7}
    remote = {"cost_usd": None, "input_tokens": None, "turns": 3}

    merged = bench_run.merge_usage(events, None, {}, remote)

    assert merged["cost_usd"] == 0.42
    assert merged["input_tokens"] == 1200
    assert merged["turns"] == 3  # a populated, higher-priority value still wins


def test_workspace_fingerprint_sees_edits_in_an_ignored_nested_workspace(tmp_path):
    """A build-from-scratch workspace has no .git of its own and lives under the
    lab repo, where /workspace/ is git-ignored. git then answers for the
    enclosing repo, its status/diff never change, and every such run used to
    stop as `no_progress` at step 2 whatever the agent did."""
    import subprocess

    lab = tmp_path / "lab"
    lab.mkdir()
    ident = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(lab)], check=True)
    (lab / ".gitignore").write_text("/workspace/\n")
    subprocess.run(["git", "-C", str(lab), "add", "-A"], check=True)
    subprocess.run(["git", *ident, "-C", str(lab), "commit", "-qm", "base"], check=True)
    ws = lab / "workspace" / "run-1"
    ws.mkdir(parents=True)
    (ws / "main.py").write_text("print(1)\n")

    fp1 = bench_run.workspace_fingerprint(ws)
    assert fp1 is not None
    assert bench_run.workspace_fingerprint(ws) == fp1  # no edits → stable
    (ws / "game.py").write_text("class Game: ...\n")
    assert bench_run.workspace_fingerprint(ws) != fp1  # new file → changes


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


def test_continuation_prompt_body_tells_the_agent_the_workspace_is_its_own(tmp_path):
    """Without the framing, the agent treats the inherited tree as a stranger's."""
    body = bench_run.render_continuation_prompt_body(
        "doom-like-K-001-C-001", "doom-like-K-001", "  Add a pause menu.  "
    )

    assert "Add a pause menu." in body
    assert "doom-like-K-001" in body
    # Absolute, for the same reason every other prompt's path is: a relative
    # workspace/<run>/ gets normalized outside the bind mount and is lost.
    assert bench_run.agent_workspace_path("doom-like-K-001-C-001") in body
    assert "restart from scratch" in body.lower()


def test_generic_continuation_prompt_never_borrows_the_tetris_framing(tmp_path):
    """A DB case out of step_prompts has no main.py and no SDL smoke test."""
    path = bench_run.generate_generic_continuation_prompt("terse-K-001", 3, tmp_path)
    text = path.read_text()

    assert path.name == "step-3-prompt.md"
    assert "SDL_VIDEODRIVER" not in text
    assert "Tetris" not in text
    assert "main.py" not in text
    assert bench_run.agent_workspace_path("terse-K-001") in text
    assert "ALL MILESTONES COMPLETE" in text


def test_tetris_continuation_prompt_still_carries_its_own_framing(tmp_path):
    """The curated lane keeps the prompt written for it."""
    path = bench_run.generate_continuation_prompt("tetris-v1-K-001", 2, tmp_path)
    text = path.read_text()

    assert "SDL_VIDEODRIVER=dummy" in text
    assert "main.py" in text


def test_stamp_event_line_keeps_json_valid():
    import json

    stamped = bench_run.stamp_event_line(
        '{"type":"assistant","message":{}}\n', now_iso="2026-08-27T20:00:00Z"
    )
    event = json.loads(stamped)
    assert event["_received_at"] == "2026-08-27T20:00:00Z"
    assert event["type"] == "assistant"


def test_stamp_event_line_leaves_non_json_untouched():
    assert bench_run.stamp_event_line("plain text\n") == "plain text\n"
    # `{}` cannot take a prepended key without corrupting the object.
    assert bench_run.stamp_event_line("{}\n") == "{}\n"


def _managed_run(
    tmp_path,
    monkeypatch,
    rc: int,
    *,
    spec_extra: dict | None = None,
    argv_extra: tuple[str, ...] = (),
    calls: dict | None = None,
) -> tuple[int, Path, dict]:
    """Drive `main()` down the managed path with a harness that exits `rc`.

    `calls` opts into recording which lab-root mutations were attempted: the
    named functions write the lab's own adapter files, and a masked harness
    condition must not call them at all.
    """
    monkeypatch.setattr(bench_run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bench_run, "DATA_DIR", tmp_path / "data" / "runs")
    monkeypatch.setattr(bench_run, "WORKSPACE_ROOT", tmp_path / "workspace")
    for name in (
        "_load_env",
        "apply_gateway_overlay",
        "restore_full_skill_views",
        "write_bench_context",
        "apply_skill_views",
    ):
        if calls is not None and name in calls:
            monkeypatch.setattr(
                bench_run,
                name,
                lambda *_a, _n=name, **_k: calls.__setitem__(_n, calls[_n] + 1),
            )
        else:
            monkeypatch.setattr(bench_run, name, lambda *_a, **_k: None)
    if calls is not None and "build_effective_skills" in calls:
        monkeypatch.setattr(
            bench_run,
            "build_effective_skills",
            lambda *_a, **_k: calls.__setitem__(
                "build_effective_skills", calls["build_effective_skills"] + 1
            )
            or [],
        )
    else:
        monkeypatch.setattr(bench_run, "build_effective_skills", lambda *_a, **_k: [])
    monkeypatch.setattr(bench_run, "copy_to_clipboard", lambda *_a, **_k: False)
    monkeypatch.setattr(bench_run.shutil, "which", lambda name: f"/usr/bin/{name}")
    for name in ("pull_usage_litellm", "pull_usage_langfuse", "pull_usage_opencode_db"):
        monkeypatch.setattr(bench_run, name, lambda *_a, **_k: None)

    seen: dict = {}

    def fake_run(cmd, log_path, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("SSE stream closed unexpectedly\n")
        return rc

    monkeypatch.setattr(bench_run, "run_opencode", fake_run)

    out_dir = tmp_path / "data" / "runs" / "r-1"
    out_dir.mkdir(parents=True)
    spec = {
        "case_id": "db-case-1",
        "prompt": "Build it.",
        "prompt_variant": "sdlc",
        "repo": "",
    }
    spec.update(spec_extra or {})
    (out_dir / "case_spec.json").write_text(json.dumps(spec))
    code = bench_run.main(
        [
            "--case",
            "db-case-1",
            "--run",
            "r-1",
            "--harness",
            "claude",
            "--executor-model",
            "stub",
            "--score-mode",
            "manual",
            "--fresh",
            *argv_extra,
        ]
    )
    return code, out_dir, seen


def test_a_dead_harness_exits_non_zero_but_keeps_its_evidence(tmp_path, monkeypatch):
    """A crashed harness must not archive as a legitimate attempt (ADR-0046).

    This exit used to be 0, so the worker — which reads the exit code, not
    usage.json — recorded `state=completed` and auto-scored the run 0: a dead
    connection entered the corpus as a model that could not solve the case.
    The non-zero return comes *after* usage.json and the archive, because the
    evidence of why is the whole point.
    """
    code, out_dir, _ = _managed_run(tmp_path, monkeypatch, rc=2)

    assert code != 0
    usage = json.loads((out_dir / "usage.json").read_text())
    assert usage["stop_reason"] == "harness_error"
    # The archive survives the non-zero exit: the return sits after it.
    assert (out_dir / "workspace").is_dir()


def test_a_harness_that_finished_still_exits_zero(tmp_path, monkeypatch):
    code, out_dir, _ = _managed_run(tmp_path, monkeypatch, rc=0)

    assert code == 0
    assert json.loads((out_dir / "usage.json").read_text()).get("stop_reason") is None


# --------------------------------------------------------------------------
# Harness conditions in the runner (ADR-0049)
# --------------------------------------------------------------------------


def test_a_bare_run_never_writes_to_the_lab_tree(tmp_path, monkeypatch):
    """A bare arm must not touch what a bare arm cannot see.

    `restore_full_skill_views` and `apply_skill_views` rewrite the lab's own
    adapter files (CLAUDE.md, OPENCODE.md, `.claude/skills`), which in a masked
    container are read-only empty binds: the write would raise, and if it
    succeeded it would refill the very mask that makes the arm bare. Same for
    BENCH_CONTEXT.md.
    """
    calls = {
        "restore_full_skill_views": 0,
        "apply_skill_views": 0,
        "write_bench_context": 0,
        "build_effective_skills": 0,
    }
    code, out_dir, seen = _managed_run(
        tmp_path,
        monkeypatch,
        rc=0,
        spec_extra={"harness_source": "bare"},
        argv_extra=("--disable-skill", "brainstorming"),
        calls=calls,
    )

    assert code == 0
    assert calls == dict.fromkeys(calls, 0)
    # The agent runs from the workspace, not the lab root.
    assert seen["cwd"] == tmp_path / "workspace" / "r-1"
    meta = json.loads((out_dir / "metadata.json").read_text())
    assert meta["harness_source"] == "bare"
    # No catalog and no subagents, stated rather than inferred from the masks.
    assert meta["skills_bundle"] == []
    assert meta["subagents"] == []
    # The declaration survives for the condition hash even though nothing was
    # ablated: it is what makes the arm addressable.
    assert meta["skills_disabled"] == ["brainstorming"]
    assert meta["harness_git_dirty"] is None
    assert meta["harness_fingerprint"]["harness_root"] == "workspace"
    assert "current working directory" in (out_dir / "prompt.md").read_text()


def test_a_bare_run_reads_its_condition_before_it_acts_on_it(tmp_path, monkeypatch):
    """The reorder, pinned.

    `--disable-skill <unknown>` is rejected by `build_effective_skills`, which
    used to run before `harness_source` was read. A bare run has no catalog to
    check the name against, so reaching exit 0 here proves the condition is now
    known before anything acts on it.
    """
    code, _out_dir, _seen = _managed_run(
        tmp_path,
        monkeypatch,
        rc=0,
        spec_extra={"harness_source": "bare"},
        argv_extra=("--disable-skill", "no-such-skill-anywhere"),
    )

    assert code == 0


def test_a_bare_run_refuses_to_stage_or_invoke_a_skill(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        _managed_run(
            tmp_path,
            monkeypatch,
            rc=0,
            spec_extra={"harness_source": "bare"},
            argv_extra=("--invoke-skill", "brainstorming"),
        )

    assert "bare" in str(excinfo.value)


def test_a_bare_run_refuses_an_unmasked_lab_root(tmp_path, monkeypatch):
    """The tripwire: on the host lane nothing masks the lab (ADR-0049).

    The workspace lives under the repo root, so a readable CLAUDE.md there is
    an ancestor memory file — the control arm would be a second treatment arm
    and nothing downstream would ever say so.
    """
    (tmp_path / "CLAUDE.md").write_text("# the lab's governance\n")

    with pytest.raises(SystemExit) as excinfo:
        _managed_run(tmp_path, monkeypatch, rc=0, spec_extra={"harness_source": "bare"})

    assert "masked" in str(excinfo.value) or "readable" in str(excinfo.value)


def test_a_lab_root_run_still_regenerates_its_skill_views(tmp_path, monkeypatch):
    """The gate must not be so broad that it breaks the ordinary ablation."""
    calls = {
        "restore_full_skill_views": 0,
        "apply_skill_views": 0,
        "build_effective_skills": 0,
    }
    code, _out_dir, seen = _managed_run(
        tmp_path,
        monkeypatch,
        rc=0,
        argv_extra=("--disable-skill", "brainstorming"),
        calls=calls,
    )

    assert code == 0
    assert calls["restore_full_skill_views"] == 1
    assert calls["apply_skill_views"] == 1
    assert calls["build_effective_skills"] == 1
    # And it runs from the lab root, which is the whole point of the condition.
    assert seen["cwd"] is None


# --------------------------------------------------------------------------
# "Needs a clone" and "is SWE-bench" are different questions
# --------------------------------------------------------------------------


def test_a_target_paired_case_needs_a_clone_but_is_not_swe_bench():
    """The conflation that invalidated both talos experiments.

    `source: "git_target"` is stamped on every case paired with an external
    repository (ADR-0029/0030) — a documentation review, a shell test suite,
    anything. It was read as "this is SWE-bench", so from step 2 onward those
    runs were told to resolve a GitHub issue, shown an empty list of target
    failing tests, and instructed to run `python -m pytest` in a repository
    with no Python. Both arms, both experiments.
    """
    spec = {"source": "git_target", "repo": "acme/talos", "case_type": "repo_task"}

    assert bench_run.spec_provisions_repo(spec) is True
    assert bench_run.spec_is_swe_bench(spec) is False


@pytest.mark.parametrize(
    "spec",
    [
        {"case_type": "swe_bench_instance", "repo": "pallets/flask"},
        {"case_type": "swe_pro", "repo": "a/b"},
        {"repo": "astropy/astropy", "fail_to_pass": ["tests/test_x.py::test_y"]},
    ],
)
def test_a_real_swe_case_is_still_recognised(spec):
    assert bench_run.spec_is_swe_bench(spec) is True


def test_nothing_at_all_is_not_swe_bench():
    assert bench_run.spec_is_swe_bench(None) is False
    assert bench_run.spec_is_swe_bench({}) is False


def test_a_swe_spec_naming_no_failing_tests_stops_the_run(tmp_path):
    """A SWE-bench task IS its failing tests; without them there is no objective.

    The prompt used to render an empty bullet list under "Target failing
    test(s):" and a bare `python -m pytest` under "Run them with:" —
    instructions to run nothing. That must fail loudly instead of reaching the
    agent.
    """
    with pytest.raises(bench_run.SweSpecError):
        bench_run.generate_swe_continuation_prompt(
            "flask-K-001",
            2,
            tmp_path,
            {"case_type": "swe_bench_instance", "fail_to_pass": []},
            None,
        )


def test_a_swe_continuation_still_names_its_tests(tmp_path):
    path = bench_run.generate_swe_continuation_prompt(
        "flask-K-001",
        2,
        tmp_path,
        {"case_type": "swe_bench_instance", "fail_to_pass": ["tests/t.py::test_a"]},
        None,
    )
    text = path.read_text()

    assert "Target failing test(s):" in text
    assert "tests/t.py::test_a" in text


def test_the_generic_continuation_says_nothing_about_failing_tests(tmp_path):
    """What a target-paired non-SWE case gets instead, at step 2."""
    path = bench_run.generate_generic_continuation_prompt("talos-K-001", 2, tmp_path)
    text = path.read_text()

    assert "Target failing test" not in text
    assert "SWE-bench" not in text
    assert "python -m pytest" not in text


def test_usage_json_records_which_prompt_each_step_was_given(tmp_path):
    """The leak showed up in no stored number. Now it shows up in one."""
    path = bench_run.write_usage_json(
        tmp_path, {"steps": 2, "prompt_kinds": ["case", "generic"]}
    )
    stored = json.loads(path.read_text())

    assert stored["prompt_kinds"] == ["case", "generic"]


def test_a_usage_skeleton_names_the_field_even_when_empty(tmp_path):
    stored = json.loads(bench_run.write_usage_json(tmp_path, None).read_text())

    assert "prompt_kinds" in stored


def test_finalize_workspace_includes_files_the_agent_created(tmp_path):
    """`git diff <ref>` lists tracked files only. Two talos arms each wrote a
    new test file and shipped an empty patch; the judge scored one of them 0
    for a file a PASS check had just executed."""
    import subprocess

    workspace = tmp_path / "workspace" / "talos-K-001"
    workspace.mkdir(parents=True)
    git = lambda *args: subprocess.run(  # noqa: E731
        ["git", "-C", str(workspace), *args], check=True, capture_output=True, text=True
    )
    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (workspace / "lib.sh").write_text("echo old\n")
    (workspace / ".gitignore").write_text("build/\n")
    git("add", "-A")
    git("commit", "-q", "-m", "provisioned")
    # The agent: edits a tracked file, creates a new one, and leaves build output.
    (workspace / "lib.sh").write_text("echo new\n")
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_new.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (workspace / "build").mkdir()
    (workspace / "build" / "out.bin").write_text("x")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    bench_run.finalize_workspace(out_dir, workspace, "talos-K-001")

    patch = (out_dir / "diff.patch").read_text()
    assert "+echo new" in patch
    assert "tests/test_new.sh" in patch and "+exit 0" in patch
    assert "build/out.bin" not in patch


# --- seeding a workspace from a case's starter files (ADR-0068) ---------------


def _starter(root):
    starter = root / "benchmarks" / "fixit" / "starter"
    (starter / "src").mkdir(parents=True)
    (starter / "index.html").write_text("<h1>broken</h1>\n")
    (starter / "src" / "app.js").write_text("console.log(1)\n")
    return starter


def test_a_starter_dir_seeds_an_empty_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(bench_run, "REPO_ROOT", tmp_path)
    _starter(tmp_path)
    ws = tmp_path / "workspace" / "run-1"
    ws.mkdir(parents=True)

    copied = bench_run.seed_workspace(ws, {"starter_dir": "benchmarks/fixit/starter"})

    assert copied == 2
    assert (ws / "index.html").read_text() == "<h1>broken</h1>\n"
    assert (ws / "src" / "app.js").exists()


def test_seeding_never_overwrites_a_workspace_that_has_files(tmp_path, monkeypatch):
    """A resumed or continued run already holds the agent's work."""
    monkeypatch.setattr(bench_run, "REPO_ROOT", tmp_path)
    _starter(tmp_path)
    ws = tmp_path / "workspace" / "run-1"
    ws.mkdir(parents=True)
    (ws / "index.html").write_text("<h1>agent's fix</h1>\n")

    assert (
        bench_run.seed_workspace(ws, {"starter_dir": "benchmarks/fixit/starter"}) == 0
    )
    assert (ws / "index.html").read_text() == "<h1>agent's fix</h1>\n"


@pytest.mark.parametrize(
    "starter", ["../outside", "/etc", "benchmarks/../../etc", "src/harness_lab_app"]
)
def test_a_starter_outside_benchmarks_is_refused(tmp_path, monkeypatch, starter):
    monkeypatch.setattr(bench_run, "REPO_ROOT", tmp_path)
    (tmp_path / "src" / "harness_lab_app").mkdir(parents=True)
    ws = tmp_path / "workspace" / "run-1"
    ws.mkdir(parents=True)
    with pytest.raises(SystemExit):
        bench_run.seed_workspace(ws, {"starter_dir": starter})


def test_a_symlink_in_a_starter_is_not_followed_out_of_it(tmp_path, monkeypatch):
    monkeypatch.setattr(bench_run, "REPO_ROOT", tmp_path)
    starter = _starter(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("token")
    (starter / "leak.txt").symlink_to(secret)
    ws = tmp_path / "workspace" / "run-1"
    ws.mkdir(parents=True)

    bench_run.seed_workspace(ws, {"starter_dir": "benchmarks/fixit/starter"})

    # Links are skipped: a starter can never carry a host file into a run.
    assert not (ws / "leak.txt").exists()
    assert not (ws / "leak.txt").is_symlink()


def test_no_starter_dir_seeds_nothing(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert bench_run.seed_workspace(ws, {}) == 0
    assert bench_run.seed_workspace(ws, None) == 0


def test_a_harness_error_names_its_cause_in_one_short_line(tmp_path):
    """K-006 stored 1000 chars of tail and the cause was not in them: LiteLLM
    repeats the provider error twice with its fallback table. The verdict line
    must carry the provider's error code on its own, short enough to survive."""
    log = tmp_path / "step-1.log"
    log.write_text(
        "# claude session init (model claude-opus-5-5)\n"
        'API Error: 400 litellm.BadRequestError: AnthropicException - {"type":"error",'
        '"error":{"type":"invalid_request_error","message":"Claude Code 2.1.197 does not '
        'support this model; version 2.1.280 or newer is required.","details":'
        '{"error_code":"claude_code_version_too_old"}}}'
        + " Fallbacks=[...]" * 80
        + "\n"
    )
    line = bench_run.harness_error_cause(log)
    assert line is not None
    assert "claude_code_version_too_old" in line
    assert "2.1.280" in line
    assert len(line) <= 300


def test_a_rejected_model_is_named_too(tmp_path):
    log = tmp_path / "step-1.log"
    log.write_text(
        "API Error: 400 anthropic_messages: Invalid model name passed in "
        "model=claude-haiku-5-5. Call `/v1/models` to view available models.\n"
    )
    line = bench_run.harness_error_cause(log)
    assert line is not None and "Invalid model name" in line


def test_no_api_error_means_no_cause_line(tmp_path):
    log = tmp_path / "step-1.log"
    log.write_text("# claude session init\nTraceback: SyntaxError\n")
    assert bench_run.harness_error_cause(log) is None


def test_a_workspace_with_a_virtualenv_still_archives(tmp_path):
    """eda-ml-iris-categ-numerico-K-008 (2026-10-23): the agent created `venv/`
    in its workspace, `git diff` returned 2.6 MB of binary, and decoding it as
    UTF-8 killed the run after the agent had finished its work."""
    dest = tmp_path / "workspace"
    (dest / "venv" / "lib").mkdir(parents=True)
    (dest / "venv" / "lib" / "blob.so").write_bytes(bytes(range(256)) * 64)
    (dest / "__pycache__").mkdir()
    (dest / "__pycache__" / "m.cpython-311.pyc").write_bytes(b"\xa4\x00\xff")
    (dest / "main.py").write_text("print('hi')\n")
    lines = bench_run.write_diff_patch(tmp_path, dest)
    patch = (tmp_path / "diff.patch").read_text()
    assert lines > 0
    assert "main.py" in patch
    # The agent's code is the diff; installed packages and caches are not.
    assert "venv/" not in patch and "__pycache__" not in patch
    # The archive itself keeps everything.
    assert (dest / "venv" / "lib" / "blob.so").exists()


def test_git_output_that_is_not_utf8_never_raises(tmp_path):
    (tmp_path / "f.bin").write_bytes(b"\xa4\xff\x00 binary")
    result = bench_run._run_git(
        ["git", "diff", "--no-index", "--text", "/dev/null", str(tmp_path / "f.bin")],
        cwd=tmp_path,
    )
    assert isinstance(result.stdout, str)


# --------------------------------------------------------------------------
# Usage: opencode sessions joined by id, one clock for every arm
# --------------------------------------------------------------------------

# Verbatim shape of opencode 1.19.5 `--print-logs` lines. The bare arm's session
# lives under the run's workspace (ADR-0049 masking); the full-harness arm's
# under /app. The old title/directory join matched neither reliably.
_BARE_LOG = (
    "timestamp=2026-11-07T19:51:47.755Z level=INFO run=c313e96e message=created "
    "id=ses_bareMain01 slug=mighty-pixel version=1.19.5 projectID=499d "
    'directory=/app/workspace/tetris-v2-K-035 path="" workspaceID=undefined '
    'parentID=undefined title="Tetris in pygame"\n'
    "timestamp=2026-11-07T19:51:48.082Z level=INFO run=c313e96e message=stream "
    "providerID=lmstudio modelID=prism-ml/bonsai-27b session.id=ses_bareMain01 "
    "small=true agent=title mode=primary\n"
)
_FULL_LOG = (
    "timestamp=2026-11-07T20:01:00.000Z level=INFO run=aa message=created "
    "id=ses_fullMain01 slug=neon-mountain version=1.19.5 projectID=499d "
    'directory=/app path="" workspaceID=undefined parentID=undefined\n'
    "timestamp=2026-11-07T20:02:00.000Z level=INFO run=aa message=created "
    "id=ses_fullChild1 slug=sub version=1.19.5 projectID=499d "
    'directory=/app path="" workspaceID=undefined parentID=ses_fullMain01\n'
)


def _opencode_db(path: Path, sessions: list[dict]) -> Path:
    """A minimal opencode.db with the three tables the reader touches."""
    import sqlite3

    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, title TEXT,
            tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,
            tokens_cache_read INTEGER, tokens_cache_write INTEGER, cost REAL
        );
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, session_id TEXT, data TEXT);
        """
    )
    for n, s in enumerate(sessions):
        con.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                s["id"],
                s.get("parent_id"),
                s.get("directory", "/app"),
                s.get("title", "an invented title"),
                s.get("input", 0),
                s.get("output", 0),
                s.get("reasoning", 0),
                s.get("cache_read", 0),
                s.get("cache_write", 0),
                s.get("cost", 0.0),
            ),
        )
        for turn in range(s.get("turns", 0)):
            con.execute(
                "INSERT INTO message VALUES (?,?,?)",
                (
                    f"msg_{n}_{turn}",
                    s["id"],
                    json.dumps(
                        {
                            "role": "assistant",
                            "time": {"created": 0, "completed": 30_000},
                        }
                    ),
                ),
            )
        for call in range(s.get("tools", 0)):
            con.execute(
                "INSERT INTO part VALUES (?,?,?)",
                (f"prt_{n}_{call}", s["id"], json.dumps({"type": "tool"})),
            )
    con.commit()
    con.close()
    return path


def test_session_ids_come_from_the_run_logs_for_both_runner_layouts(tmp_path):
    bare = tmp_path / "step-1.log"
    bare.write_text(_BARE_LOG)
    full = tmp_path / "step-2.log"
    full.write_text(_FULL_LOG + _FULL_LOG)  # a repeated line counts once

    ids = bench_run.opencode_session_ids([bare, full, tmp_path / "missing.log"])

    assert ids == ["ses_bareMain01", "ses_fullMain01", "ses_fullChild1"]


def test_a_masked_bare_session_is_counted_whatever_its_title_or_directory(tmp_path):
    """Every bare arm recorded no tokens: its session sits under
    /app/workspace/<run>, and the old query demanded directory = /app plus a
    title containing the run id — a title the model writes, not the lab."""
    db = _opencode_db(
        tmp_path / "opencode.db",
        [
            {
                "id": "ses_bareMain01",
                "directory": "/app/workspace/tetris-v2-K-035",
                "title": "Tetris in pygame",
                "input": 100_000,
                "output": 2_000,
                "reasoning": 500,
                "turns": 4,
                "tools": 3,
            },
            # Another run's session in the same DB must not leak in.
            {"id": "ses_otherRun", "input": 9_999_999, "output": 9_999},
        ],
    )

    usage = bench_run.pull_usage_opencode_db(["ses_bareMain01"], db_path=db)

    assert usage is not None
    assert usage["input_tokens"] == 100_000
    # Generated tokens include reasoning: the model produced them either way.
    assert usage["output_tokens"] == 2_500
    assert usage["reasoning_tokens"] == 500
    assert usage["turns"] == 4
    assert usage["tool_calls"] == 3
    assert usage["model_minutes"] == 2.0  # 4 turns x 30 s
    assert usage["usage_source"] == "opencode_db"
    # opencode reports 0 for an unpriced model; that is left to the
    # self-hosted rule, not recorded as a measured price here.
    assert "cost_usd" not in usage


def test_sub_agent_sessions_are_summed_with_their_parent(tmp_path):
    db = _opencode_db(
        tmp_path / "opencode.db",
        [
            {"id": "ses_fullMain01", "input": 50_000, "output": 1_000, "cache_read": 7},
            # Not announced in the log, but its parent was: still this run's.
            {"id": "ses_hiddenKid", "parent_id": "ses_fullMain01", "input": 5_000},
        ],
    )

    usage = bench_run.pull_usage_opencode_db(["ses_fullMain01"], db_path=db)

    assert usage["sessions"] == 2
    assert usage["input_tokens"] == 55_000
    # The key collect_cost reads; `tokens_cache_read` was never picked up.
    assert usage["cache_read_tokens"] == 7


def test_no_sessions_or_no_db_is_unknown_never_zero(tmp_path):
    db = _opencode_db(tmp_path / "opencode.db", [{"id": "ses_x", "input": 1}])
    assert bench_run.pull_usage_opencode_db([], db_path=db) is None
    assert bench_run.pull_usage_opencode_db(["ses_absent"], db_path=db) is None
    assert (
        bench_run.pull_usage_opencode_db(["ses_x"], db_path=tmp_path / "nope.db")
        is None
    )


@pytest.mark.parametrize(
    ("harness", "models", "expected"),
    [
        ("opencode", ["lmstudio/prism-ml/bonsai-27b"], 0.0),
        ("opencode", ["ollama/qwen3.6:27b"], 0.0),
        ("opencode", ["gateway/lmstudio-qwen3.6-35b"], 0.0),
        # One cloud model in the run and nothing can be said about spend.
        ("opencode", ["lmstudio/a", "gateway/gpt-5"], None),
        ("opencode", ["gateway/claude-sonnet"], None),
        # claude/codex drop a local id and use their own cloud default.
        ("claude", ["lmstudio/prism-ml/bonsai-27b"], None),
        ("opencode", [], None),
    ],
)
def test_self_hosted_cost_is_zero_only_when_every_model_is_local(
    harness, models, expected
):
    assert bench_run.self_hosted_cost(harness, models) == expected


def test_both_arms_get_the_same_usage_fields(tmp_path, monkeypatch):
    """The bare and full-harness arms of one experiment must be measured by the
    same instrument; a field one arm has and the other lacks is a gap in the
    comparison, not a difference between harnesses."""
    db = _opencode_db(
        tmp_path / "opencode.db",
        [
            {
                "id": "ses_bareMain01",
                "directory": "/app/workspace/r",
                "input": 10,
                "output": 2,
                "turns": 1,
                "tools": 1,
            },
            {"id": "ses_fullMain01", "input": 30, "output": 4, "turns": 2, "tools": 1},
            {"id": "ses_fullChild1", "parent_id": "ses_fullMain01", "input": 5},
        ],
    )
    monkeypatch.setattr(bench_run, "opencode_db_path", lambda: db)
    for name in ("pull_usage_litellm", "pull_usage_langfuse"):
        monkeypatch.setattr(bench_run, name, lambda *_a, **_k: None)

    recorded = {}
    for arm, log_text in (("bare", _BARE_LOG), ("full", _FULL_LOG)):
        out_dir = tmp_path / arm
        (out_dir / "steps").mkdir(parents=True)
        (out_dir / "steps" / "step-1.log").write_text(log_text)
        recorded[arm] = bench_run.assemble_usage(
            out_dir,
            out_dir / "opencode.log",
            run_id=f"run-{arm}",
            session_id=f"bench-run-{arm}",
            harness="opencode",
            models=["lmstudio/prism-ml/bonsai-27b"],
            harness_seconds=90.0,
            completed_steps=1,
            stop_reason="completion_signal",
            prompt_kinds=["case"],
        )

    measured = ("input_tokens", "output_tokens", "turns", "tool_calls", "cost_usd")
    for arm, usage in recorded.items():
        for key in measured:
            assert usage.get(key) is not None, (arm, key)
        assert usage["time_minutes"] == 1.5, arm
        assert usage["time_source"] == "step_clock"
        assert usage["cost_usd"] == 0.0
        assert usage["cost_source"] == "self_hosted"
    assert recorded["full"]["input_tokens"] == 35  # child session included


def test_the_step_clock_is_the_recorded_time_and_spans_every_step(
    tmp_path, monkeypatch
):
    """`time_minutes` came from opencode session spans that only covered the
    sessions whose title matched — 5.8 min recorded for a 109-minute run."""
    clock = iter([0.0, 120.0, 1000.0, 1180.0])
    monkeypatch.setattr(bench_run, "step_clock", lambda: next(clock))

    code, out_dir, _ = _managed_run(
        tmp_path, monkeypatch, rc=0, argv_extra=("--max-steps", "2")
    )

    assert code == 0
    usage = json.loads((out_dir / "usage.json").read_text())
    assert usage["time_minutes"] == 5.0  # (120 + 180) s across both steps
    assert usage["time_source"] == "step_clock"
