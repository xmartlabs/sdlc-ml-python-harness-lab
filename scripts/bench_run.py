#!/usr/bin/env python3
"""Unified benchmark runner: prepare → execute → score → close.

This is the CLI backend a future web UI would call. It replaces the manual
bench-start / paste / bench-close flow with a single reproducible command.

Usage (prefer make):
    make bench-run CASE=tetris-v1 RUN=tetris-v1-A-003 PROMPT=incremental \\
        EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build SCORE_MODE=manual

    # Multi-step: run up to N opencode sessions automatically
    make bench-run ... MAX_STEPS=6 SCORE_MODE=interactive

    # Resume a run whose workspace already exists
    make bench-run ... RESUME=1 SCORE_MODE=interactive

    # Dry run: see everything that would happen, writes nothing
    make bench-run ... DRY_RUN=1

Direct:
    uv run python scripts/bench_run.py \\
        --case tetris-v1 --run tetris-v1-A-003 --prompt incremental \\
        --executor-model gateway/lmstudio-qwen3.6-35b-build --score-mode none
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench_close import _run_git, update_runs_yaml
from bench_start import (
    BENCH_CONTEXT,
    BENCH_CONTEXT_PLACEHOLDER,
    WORKSPACE_ROOT,
    copy_to_clipboard,
    patch_env_token,
    restore_env_token,
    write_bench_context,
)
from create_run import (
    DATA_DIR,
    REPO_ROOT,
    RUNS_FILES,
    _load_env,
    create_litellm_key,
    find_run,
    scaffold_report,
    set_status_running,
    update_results_csv,
)

try:
    import yaml
except ImportError:
    sys.exit("PyYAML not found. Run with: uv run python scripts/bench_run.py ...")

# ---------------------------------------------------------------------------
# Prompt variant → short name mapping
# ---------------------------------------------------------------------------

VARIANT_TO_SHORT: dict[str, str] = {
    "tetris_naive_v1": "naive",
    "tetris_incremental_v1": "incremental",
    "tetris_conductor_v1": "conductor",
    "tetris_conductor_small_steps_v1": "conductor",
    "nemotron_planner_v1": "conductor",
    "nemotron_planner_small_steps_v1": "conductor",
}

# ---------------------------------------------------------------------------
# Score sanity check: lightweight patterns to warn on implausible scores.
# (non-blocking by default; warn + confirm in interactive mode)
# ---------------------------------------------------------------------------

SCORE_CHECKS: list[tuple[int, list[str], str]] = [
    (4, ["pygame", "surface", "blit", "draw", "screen"], "pygame / drawing code"),
    (
        5,
        ["tick", "drop", "fall", "gravity", "clock", "move_down"],
        "falling / tick logic",
    ),
    (
        6,
        ["K_LEFT", "K_RIGHT", "pygame.key", "event.key", "KEYDOWN"],
        "left/right key handling",
    ),
    (7, ["rotat"], "rotation logic"),
    (
        8,
        ["collision", "lock", "freeze", "land", "check_collision"],
        "collision / lock logic",
    ),
    (
        9,
        ["clear", "line_clear", "lines_cleared", "score", "level"],
        "line clear / scoring logic",
    ),
]

# Signals that indicate the agent has finished this increment and wants a new session
_STOP_RE = re.compile(
    r"\bDONE\b"
    r"|NEXT increment"
    r"|open a new (chat|conversation|session)"
    r"|start a new (chat|conversation|session)"
    r"|stop after this increment"
    r"|implementation (is |is now )?complete"
    r"|tetris is complete"
    r"|game is complete"
    r"|milestone (complete|achieved|done)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--case", required=True, help="Benchmark case id, e.g. tetris-v1")
    p.add_argument("--run", required=True, help="Run id, e.g. tetris-v1-A-003")
    p.add_argument(
        "--prompt",
        help="Short prompt name: naive | incremental | conductor. "
        "Auto-detected from runs.yaml if omitted.",
    )
    p.add_argument(
        "--planner-model", default=None, help="Gateway model alias for planner"
    )
    p.add_argument(
        "--executor-model", default=None, help="Gateway model alias for executor"
    )
    p.add_argument(
        "--score-mode",
        choices=["manual", "interactive", "none"],
        default="manual",
        help="manual=prepare+execute only; interactive=prompt for score after run; "
        "none=prepare only, no execution",
    )
    p.add_argument(
        "--score", type=int, default=None, help="Score 0-10 (skips interactive prompt)"
    )
    p.add_argument(
        "--interventions", type=int, default=None, help="Human interventions count"
    )
    p.add_argument("--notes", default="", help="Optional notes string")
    p.add_argument(
        "--max-steps",
        type=int,
        default=1,
        metavar="N",
        help="Max number of opencode execution steps (default: 1). "
        "For incremental benchmarks, set to 6-10 to chain multiple increments automatically.",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Allow running into an existing workspace (skip workspace existence check). "
        "Use when a previous run was interrupted and you want to continue.",
    )
    p.add_argument(
        "--create-litellm-key",
        action="store_true",
        help="Attempt to create a LiteLLM virtual key for this run",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Print what would happen; write nothing"
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_task_yaml(runs_path: Path) -> dict:
    task_path = runs_path.parent / "task.yaml"
    if not task_path.exists():
        sys.exit(f"task.yaml not found at {task_path}")
    return yaml.safe_load(task_path.read_text())


def resolve_prompt_file(
    task: dict, short_name: str | None, run: dict
) -> tuple[str, Path]:
    """Return (resolved_short_name, absolute_path_to_prompt_file)."""
    if short_name is None:
        variant = run.get("prompt_variant", "")
        short_name = VARIANT_TO_SHORT.get(variant)
        if not short_name:
            sys.exit(
                f"Cannot auto-detect prompt from variant '{variant}'. "
                f"Pass --prompt explicitly (naive | incremental | conductor)."
            )
    prompts: dict = task.get("prompts", {})
    if short_name not in prompts:
        sys.exit(
            f"Prompt '{short_name}' not found in task.yaml. "
            f"Available: {list(prompts.keys())}"
        )
    return short_name, REPO_ROOT / prompts[short_name]


def extract_cell_id(run_id: str) -> str:
    parts = run_id.split("-")
    return parts[2] if len(parts) >= 4 else "?"


def discover_skills() -> list[str]:
    candidates = [
        REPO_ROOT / ".opencode" / "skills",
        REPO_ROOT / ".claude" / "skills",
        REPO_ROOT / ".github" / "skills",
    ]
    found: list[str] = []
    for d in candidates:
        if d.is_dir():
            found.extend(p.name for p in d.iterdir() if p.is_dir())
    return sorted(set(found))


def discover_subagents() -> list[str]:
    d = REPO_ROOT / ".github" / "agents"
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.md") if p.stem != "README")


def generate_prompt(
    run_id: str,
    prompt_short: str,
    prompt_source: Path,
    planner_model: str | None,
    executor_model: str | None,
    session_id: str,
    out_dir: Path,
) -> Path:
    header = (
        f"<!-- BENCHMARK RUN METADATA\n"
        f"BENCHMARK_ID: {run_id.rsplit('-', 2)[0]}\n"
        f"RUN_ID: {run_id}\n"
        f"SESSION_ID: {session_id}\n"
        f"PROMPT_VARIANT: {prompt_short}\n"
        f"PLANNER_MODEL: {planner_model or ''}\n"
        f"EXECUTOR_MODEL: {executor_model or ''}\n"
        f"-->\n\n"
        f"> All files MUST be created in `workspace/{run_id}/`\n"
        f"> Entry point: `workspace/{run_id}/main.py`\n\n"
        f"---\n\n"
    )
    prompt_path = out_dir / "prompt.md"
    prompt_path.write_text(header + prompt_source.read_text())
    return prompt_path


def generate_continuation_prompt(run_id: str, step_n: int, steps_dir: Path) -> Path:
    """Write a continuation prompt for step N (step > 1) in an incremental run."""
    text = (
        f"<!-- Continuation step {step_n} for RUN_ID={run_id} -->\n\n"
        f"This is continuation step {step_n} for RUN_ID={run_id}.\n\n"
        f"Continue from the existing workspace. Do NOT restart from scratch.\n"
        f"Do NOT rewrite unrelated code.\n\n"
        f"1. Inspect `workspace/{run_id}/main.py` and understand the current state.\n"
        f"2. Implement only the next missing Tetris milestone.\n"
        f"3. Validate with: `python workspace/{run_id}/main.py`\n"
        f"4. Stop after ONE verifiable increment.\n"
        f"5. State clearly what you implemented and what the next recommended increment is.\n"
        f"6. If the game is complete (all milestones done), say: DONE\n"
    )
    path = steps_dir / f"step-{step_n}-prompt.md"
    path.write_text(text)
    return path


def write_metadata(
    out_dir: Path, args: argparse.Namespace, task: dict, run: dict, prompt_short: str
) -> Path:
    meta = {
        "project": "ai-sdlc-lab",
        "benchmark_id": task.get("task_id", args.case),
        "case": args.case,
        "run_id": args.run,
        "cell_id": extract_cell_id(args.run),
        "session_id": run.get("langfuse_session_id", f"bench-{args.run}"),
        "methodology": run.get("methodology", ""),
        "harness": run.get("harness", ""),
        "prompt_variant": prompt_short,
        "planner_model": args.planner_model,
        "executor_model": args.executor_model,
        "score_mode": args.score_mode,
        "max_steps": args.max_steps,
        "skills_bundle": discover_skills(),
        "subagents": discover_subagents(),
        "base_commit": task.get("base_commit", ""),
        "workspace": f"workspace/{args.run}",
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    path = out_dir / "metadata.json"
    path.write_text(json.dumps(meta, indent=2))
    return path


def write_run_env(out_dir: Path, args: argparse.Namespace, session_id: str) -> Path:
    lines = [
        f"AI_SDLC_RUN_ID={args.run}",
        f"AI_SDLC_SESSION_ID={session_id}",
        f"AI_SDLC_BENCHMARK_ID={args.case}",
        f"OPENCODE_MODEL={args.executor_model or ''}",
        f"OPENCODE_MODEL_PLAN={args.planner_model or ''}",
        f"LANGFUSE_SESSION_ID={session_id}",
    ]
    path = out_dir / "run.env"
    path.write_text("\n".join(lines) + "\n")
    return path


def validate_workspace(workspace: Path, run_id: str, resume: bool) -> None:
    """Refuse to run into an existing workspace unless --resume is passed."""
    if workspace.exists() and any(workspace.iterdir()):
        if not resume:
            sys.exit(
                f"❌ workspace/{run_id}/ already exists and is not empty.\n"
                f"   Pass RESUME=1 to continue from the existing state, or remove it:\n"
                f"     rm -rf workspace/{run_id}/"
            )
        print(
            f"  ⚠️  workspace/{run_id}/ exists — resuming from current state (--resume)."
        )


def find_next_run(runs_path: Path, current_run_id: str) -> dict | None:
    """Return the next pending run after current_run_id, or None."""
    data = yaml.safe_load(runs_path.read_text())
    runs = data.get("runs", [])
    found = False
    for run in runs:
        if run["run_id"] == current_run_id:
            found = True
            continue
        if found and run.get("status") == "pending":
            return run
    # fallback: any other pending run
    for run in runs:
        if run.get("status") == "pending" and run["run_id"] != current_run_id:
            return run
    return None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def detect_run_subcommand() -> bool:
    """Return True if `opencode run` (headless) subcommand is available."""
    try:
        result = subprocess.run(
            ["opencode", "run", "--help"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0 and "message" in (result.stdout + result.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def build_opencode_cmd(
    executor_model: str, agent: str | None, message: str
) -> list[str]:
    """Assemble the non-interactive `opencode run` command.

    --dangerously-skip-permissions is required for headless runs so the agent
    does not block waiting for interactive permission approvals.
    """
    cmd = ["opencode", "run", "--dangerously-skip-permissions", "--print-logs"]
    if executor_model:
        cmd += ["-m", executor_model]
    if agent:
        cmd += ["--agent", agent]
    cmd.append(message)
    return cmd


def _print_manual_fallback(
    args: argparse.Namespace, executor_model: str, prompt_path: Path, clipped: bool
) -> None:
    print()
    print("-" * 66)
    print("  NEXT STEP — run manually:")
    print("-" * 66)
    print("  1. make opencode            # launches the TUI with .env + plugins loaded")
    print(f"  2. Verify model:   {executor_model or '(set EXECUTOR_MODEL)'}")
    print(
        "  3. Paste prompt:   Cmd+V" + (" (already in clipboard ✓)" if clipped else "")
    )
    if not clipped:
        print(f"     or: cat {prompt_path.relative_to(REPO_ROOT)} | pbcopy")
    print("  4. When done, close the run:")
    print(f"     make bench-close RUN={args.run} SCORE=<0-10> INTERVENTIONS=<N>")
    print("-" * 66)


def run_opencode(
    cmd: list[str], log_path: Path, also_append: Path | None = None
) -> int:
    """Run opencode, streaming output to terminal and capturing to log_path.

    If also_append is given, output is also appended to that file (combined log).
    """
    import datetime as _dt

    header = f"# opencode run started {_dt.datetime.utcnow().isoformat()}Z\n"
    cmd_line = "# cmd: " + " ".join(cmd[:-1]) + " <prompt>\n\n"

    append_fh = None
    if also_append is not None:
        append_fh = also_append.open("a")

    with log_path.open("w") as log:
        log.write(header + cmd_line)
        log.flush()
        if append_fh:
            append_fh.write(f"\n{'=' * 60}\n{header}{cmd_line}")
            append_fh.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            if append_fh:
                append_fh.write(line)
        proc.wait()

    if append_fh:
        append_fh.close()

    return proc.returncode


def detect_stop_signal(log_text: str) -> bool:
    """Return True if the agent's output contains a completion/next-chat signal."""
    return bool(_STOP_RE.search(log_text))


# ---------------------------------------------------------------------------
# Usage enrichment (all fail silently)
# ---------------------------------------------------------------------------


def parse_log_usage(log_path: Path) -> dict | None:
    """Extract approximate usage from opencode output log (best-effort)."""
    if not log_path.exists():
        return None
    text = log_path.read_text(errors="replace")
    result: dict = {}

    # Step count (count our own header markers)
    steps = len(re.findall(r"^# opencode run started", text, re.MULTILINE))
    if steps > 1:
        result["steps"] = steps

    # Tool calls (opencode prints tool invocations)
    tool_calls = len(
        re.findall(
            r"(?:Running tool|Tool call|Calling tool|tool_use|ToolUse)",
            text,
            re.IGNORECASE,
        )
    )
    if tool_calls:
        result["tool_calls"] = tool_calls

    # Rough turn count (assistant response markers)
    turns = len(
        re.findall(
            r"(?:^Assistant:|>\s*Assistant|<assistant|role.*assistant)",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
    )
    if turns:
        result["turns"] = turns

    # Files touched (write/edit/create patterns)
    files_touched = set(
        re.findall(
            r"(?:Writing to|Editing|Creating|Wrote|Created)\s+[`\"']?([\w./\-]+\.py)[`\"']?",
            text,
            re.IGNORECASE,
        )
    )
    if files_touched:
        result["files_touched"] = sorted(files_touched)

    # Detected model
    m = re.search(
        r"(?:Using model|model)[:\s]+([a-zA-Z0-9/._:\-]+)", text, re.IGNORECASE
    )
    if m:
        result["detected_model"] = m.group(1)

    # Detected session/provider
    s = re.search(r"session[_\-]?id[:\s]+([a-zA-Z0-9\-]+)", text, re.IGNORECASE)
    if s:
        result["detected_session"] = s.group(1)

    return result if result else None


def pull_usage_litellm(run_id: str) -> dict | None:
    base = os.environ.get("GATEWAY_BASE_URL", "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    master_key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not base or not master_key:
        return None
    try:
        url = f"{base}/spend/logs?key_alias={run_id}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {master_key}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
            data = json.loads(resp.read())
        if isinstance(data, list) and data:
            total_input = sum(r.get("prompt_tokens", 0) for r in data)
            total_output = sum(r.get("completion_tokens", 0) for r in data)
            total_cost = sum(r.get("spend", 0) for r in data)
            return {
                "requests": len(data),
                "input_tokens": total_input or None,
                "output_tokens": total_output or None,
                "cost_usd": round(total_cost, 6) if total_cost else None,
            }
    except Exception:
        pass
    return None


def pull_usage_langfuse(session_id: str) -> dict | None:
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
    if not secret or not public:
        return None
    try:
        import base64

        creds = base64.b64encode(f"{public}:{secret}".encode()).decode()
        url = f"{host.rstrip('/')}/api/public/sessions/{session_id}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Basic {creds}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
            data = json.loads(resp.read())
        traces = data.get("traces", [])
        if traces:
            input_t = sum(t.get("usage", {}).get("input", 0) for t in traces)
            output_t = sum(t.get("usage", {}).get("output", 0) for t in traces)
            cost = sum(t.get("usage", {}).get("totalCost", 0) for t in traces)
            return {
                "turns": len(traces),
                "input_tokens": input_t or None,
                "output_tokens": output_t or None,
                "cost_usd": round(cost, 6) if cost else None,
            }
    except Exception:
        pass
    return None


def write_usage_json(out_dir: Path, usage: dict | None) -> Path:
    skeleton: dict = {
        "turns": None,
        "tool_calls": None,
        "steps": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "time_minutes": None,
        "files_modified": [],
        "diff_lines": None,
        "detected_model": None,
        "detected_session": None,
    }
    if usage:
        skeleton.update(usage)
    path = out_dir / "usage.json"
    path.write_text(json.dumps(skeleton, indent=2))
    return path


# ---------------------------------------------------------------------------
# Score sanity check
# ---------------------------------------------------------------------------


def score_sanity_check(
    workspace: Path, run_id: str, score: int, interactive: bool
) -> bool:
    """Check main.py for evidence matching the claimed score.

    Non-blocking by default. In interactive mode, asks for confirmation when
    patterns are missing.
    """
    main_py = workspace / run_id / "main.py"
    if not main_py.exists():
        main_py = workspace / "main.py"
    if not main_py.exists():
        return True  # can't check, don't block

    text = main_py.read_text(errors="replace")
    text_lower = text.lower()
    warnings = []

    for threshold, patterns, label in SCORE_CHECKS:
        if score >= threshold and not any(p.lower() in text_lower for p in patterns):
            warnings.append(
                f"  ⚠️  score={score} >= {threshold} but no {label} found in main.py"
            )

    if not warnings:
        return True

    print()
    for w in warnings:
        print(w)

    if interactive:
        try:
            ans = (
                input("  Score looks high for the code found — proceed anyway? [y/N]: ")
                .strip()
                .lower()
            )
            return ans in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            print()
            return False

    return True  # non-blocking when not interactive


# ---------------------------------------------------------------------------
# MLflow (optional)
# ---------------------------------------------------------------------------


def log_mlflow(
    args: argparse.Namespace,
    run: dict,
    score_data: tuple[int, int, str] | None,
    usage: dict | None,
    out_dir: Path,
) -> None:
    uri = os.environ.get("MLFLOW_TRACKING_URI", "")
    exp_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "ai-sdlc-lab")
    if not uri:
        return
    try:
        import mlflow
    except ImportError:
        print("  MLflow not installed — skipping (pip install mlflow to enable)")
        return
    try:
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(exp_name)
        with mlflow.start_run(run_name=args.run):
            mlflow.log_params(
                {
                    "case": args.case,
                    "run_id": args.run,
                    "cell_id": extract_cell_id(args.run),
                    "prompt_variant": args.prompt or run.get("prompt_variant", ""),
                    "planner_model": args.planner_model or "",
                    "executor_model": args.executor_model or "",
                    "harness": run.get("harness", ""),
                    "methodology": run.get("methodology", ""),
                    "score_mode": args.score_mode,
                    "max_steps": args.max_steps,
                }
            )
            if score_data:
                s, i, _ = score_data
                mlflow.log_metrics({"functional_score": s, "human_interventions": i})
            if usage:
                mlflow.log_metrics(
                    {
                        k: v
                        for k, v in usage.items()
                        if isinstance(v, (int, float)) and v is not None
                    }
                )
            for fname in ["metadata.json", "prompt.md", "usage.json", "report.md"]:
                fpath = out_dir / fname
                if fpath.exists():
                    mlflow.log_artifact(str(fpath))
        print(f"  MLflow run logged to {uri} / experiment={exp_name}")
    except Exception as e:
        print(f"  MLflow logging failed (non-blocking): {e}")


# ---------------------------------------------------------------------------
# Close phase (mirrors bench_close.py logic)
# ---------------------------------------------------------------------------


def close_run(
    args: argparse.Namespace,
    run: dict,
    runs_path: Path,
    out_dir: Path,
    score: int,
    interventions: int,
    notes: str,
) -> None:
    workspace = WORKSPACE_ROOT / args.run
    diff_lines = 0

    if not workspace.exists():
        print(
            f"  ⚠️  workspace/{args.run}/ not found — skipping artifact copy.\n"
            f"     Move files manually if needed:\n"
            f"     mkdir -p workspace/{args.run} && mv <files> workspace/{args.run}/"
        )
    else:
        dest = out_dir / "workspace"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(workspace, dest)
        file_count = sum(1 for _ in dest.rglob("*") if _.is_file())
        print(
            f"  Copied workspace → data/runs/{args.run}/workspace/ ({file_count} files)"
        )

        diff_path = out_dir / "diff.patch"
        result = _run_git(
            ["git", "diff", "--no-index", "/dev/null", str(dest)], cwd=REPO_ROOT
        )
        diff_path.write_text(result.stdout)
        diff_lines = result.stdout.count("\n")
        print(f"  diff.patch: {diff_lines} lines")

        if score > 3 and diff_lines == 0:
            print(
                f"  ⚠️  WARNING: score={score} but diff.patch is empty — "
                f"workspace/{args.run}/ appears to have no implementation."
            )

        shutil.rmtree(workspace)
        if WORKSPACE_ROOT.exists() and not any(WORKSPACE_ROOT.iterdir()):
            WORKSPACE_ROOT.rmdir()
        print(f"  Removed workspace/{args.run}/")

    BENCH_CONTEXT.write_text(BENCH_CONTEXT_PLACEHOLDER)
    print("  BENCH_CONTEXT.md reset")

    env_path = REPO_ROOT / ".env"
    restore_env_token(env_path)
    print("  .env GATEWAY_TOKEN restored")

    update_runs_yaml(runs_path, args.run, score, interventions, notes)
    print(
        f"  runs.yaml: status=completed  score={score}/10  interventions={interventions}"
    )

    results_path = runs_path.parent / "results.csv"
    parts = args.run.split("-")
    update_results_csv(
        results_path,
        {
            "run_id": args.run,
            "cell_id": parts[2] if len(parts) >= 4 else "",
            "date": datetime.date.today().isoformat(),
            "methodology": run.get("methodology", ""),
            "harness": run.get("harness", ""),
            "prompt_variant": run.get("prompt_variant", args.prompt or ""),
            "planner_model": args.planner_model or run.get("planner_model", ""),
            "executor_model": args.executor_model or run.get("executor_model", ""),
            "executor_context": str(run.get("executor_context", "")),
            "temperature": str(run.get("temperature", "")),
            "diff_lines": str(diff_lines) if diff_lines else "",
            "functional_level": str(score),
            "human_interventions": str(interventions),
            "langfuse_session": run.get("langfuse_session_id", ""),
            "branch": run.get("branch", ""),
            "notes": notes,
        },
    )
    print("  results.csv updated")

    files_to_stage = [
        str(out_dir.relative_to(REPO_ROOT)),
        str(runs_path.relative_to(REPO_ROOT)),
        str(results_path.relative_to(REPO_ROOT)),
        "BENCH_CONTEXT.md",
    ]
    _run_git(["git", "add", *files_to_stage], cwd=REPO_ROOT)
    notes_suffix = f", {notes}" if notes else ""
    commit_msg = (
        f"bench: close {args.run} "
        f"(score={score}/10, interventions={interventions}{notes_suffix})"
    )
    result = _run_git(["git", "commit", "-m", commit_msg], cwd=REPO_ROOT)
    if result.returncode == 0:
        sha = result.stdout.strip().split("\n")[0]
        print(f"  git commit: {sha}")
    else:
        print(
            f"  ⚠️  git commit skipped: {result.stderr.strip() or 'nothing to commit'}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _load_env()

    runs_path = RUNS_FILES.get(args.case)
    if runs_path is None:
        sys.exit(f"Unknown case '{args.case}'. Known cases: {list(RUNS_FILES.keys())}")

    task = load_task_yaml(runs_path)
    run = find_run(runs_path, args.run)

    if run.get("status") == "completed":
        sys.exit(f"Run {args.run} is already completed. Use a new run_id.")

    prompt_short, prompt_source = resolve_prompt_file(task, args.prompt, run)
    session_id = run.get("langfuse_session_id", f"bench-{args.run}")

    # Resolve models: CLI args override runs.yaml values
    executor_model = args.executor_model or run.get("executor_model") or ""
    planner_model = args.planner_model or (
        run.get("planner_model")
        if run.get("planner_model") != run.get("executor_model")
        else None
    )
    args.executor_model = executor_model
    args.planner_model = planner_model

    workspace = WORKSPACE_ROOT / args.run

    # -----------------------------------------------------------------------
    # DRY RUN
    # -----------------------------------------------------------------------
    if args.dry_run:
        print()
        print("=" * 66)
        print(f"  bench-run DRY RUN: {args.run}")
        print("=" * 66)
        print(f"  Case:            {args.case}")
        print(f"  Run:             {args.run}")
        print(f"  Cell:            {extract_cell_id(args.run)}")
        print(
            f"  Prompt variant:  {prompt_short} ({prompt_source.relative_to(REPO_ROOT)})"
        )
        print(f"  Planner model:   {planner_model or '(none)'}")
        print(f"  Executor model:  {executor_model or '(unset)'}")
        print(f"  Score mode:      {args.score_mode}")
        print(f"  Max steps:       {args.max_steps}")
        print(f"  Resume:          {args.resume}")
        print(f"  Session ID:      {session_id}")
        print()
        out_dir = DATA_DIR / args.run
        print("  Files that would be written:")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/metadata.json")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/prompt.md")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/run.env")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/report.md")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/usage.json")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/opencode.log")
        if args.max_steps > 1:
            for s in range(1, min(args.max_steps, 4) + 1):
                extra = (
                    "..." if s == min(args.max_steps, 4) and args.max_steps > 3 else ""
                )
                print(
                    f"    {out_dir.relative_to(REPO_ROOT)}/steps/step-{s}-prompt.md{extra}"
                )
                print(f"    {out_dir.relative_to(REPO_ROOT)}/steps/step-{s}.log{extra}")
        print(f"    workspace/{args.run}/")
        print()
        if detect_run_subcommand():
            preview = build_opencode_cmd(executor_model, None, "<prompt>")
            print("  Execution: " + " ".join(preview))
            if args.max_steps > 1:
                print(
                    f"  (repeated up to {args.max_steps} times; stops on completion signal)"
                )
        else:
            print("  Execution: `opencode run` not available — manual paste required")
        print()
        print("  LiteLLM metadata that would be registered:")
        print(f"    key_alias: {args.run}   metadata.session_id: {session_id}")
        print("=" * 66)
        return 0

    # -----------------------------------------------------------------------
    # PHASE 1 — PREPARE
    # -----------------------------------------------------------------------
    print()
    print("=" * 66)
    print(f"  bench-run: {args.run}")
    print("=" * 66)
    print()
    print("  [1/5] Preparing run...")

    out_dir = DATA_DIR / args.run
    out_dir.mkdir(parents=True, exist_ok=True)

    validate_workspace(workspace, args.run, args.resume)
    workspace.mkdir(parents=True, exist_ok=True)

    prompt_path = generate_prompt(
        args.run,
        prompt_short,
        prompt_source,
        planner_model,
        executor_model,
        session_id,
        out_dir,
    )
    print(f"  Prompt:      {prompt_path.relative_to(REPO_ROOT)}")

    write_metadata(out_dir, args, task, run, prompt_short)
    print(f"  Metadata:    {(out_dir / 'metadata.json').relative_to(REPO_ROOT)}")

    write_run_env(out_dir, args, session_id)
    print(f"  Env:         {(out_dir / 'run.env').relative_to(REPO_ROOT)}")

    if not (out_dir / "report.md").exists():
        scaffold_report(run, out_dir)
    print(f"  Report:      {(out_dir / 'report.md').relative_to(REPO_ROOT)}")

    write_bench_context(args.run)
    set_status_running(runs_path, args.run)

    # Virtual key (optional)
    run_key: str | None = None
    if args.create_litellm_key:
        gateway_base = os.environ.get("GATEWAY_BASE_URL", "")
        master_key = os.environ.get("LITELLM_MASTER_KEY", "")
        if not gateway_base or not master_key:
            print(
                f"  ⚠️  LiteLLM key skipped: GATEWAY_BASE_URL or LITELLM_MASTER_KEY not set.\n"
                f"     Create manually → key_alias={args.run}  metadata.session_id={session_id}"
            )
        else:
            try:
                run_key = create_litellm_key(gateway_base, master_key, run)
                key_file = out_dir / ".gateway_key"
                key_file.write_text(run_key)
                key_file.chmod(0o600)
                patch_env_token(REPO_ROOT / ".env", run_key)
                os.environ["GATEWAY_TOKEN"] = run_key
                print(
                    f"  Key:         written to {key_file.relative_to(REPO_ROOT)} (gitignored)"
                )
            except RuntimeError as e:
                print(f"  ⚠️  LiteLLM key creation failed: {e}")

    clipped = copy_to_clipboard(prompt_path.read_text())

    # -----------------------------------------------------------------------
    # PHASE 2 — EXECUTE (multi-step loop)
    # -----------------------------------------------------------------------
    print()
    print("  [2/5] Executing...")

    opencode_log = out_dir / "opencode.log"
    has_run_cmd = detect_run_subcommand()
    max_steps = max(1, args.max_steps)
    steps_dir = out_dir / "steps"
    completed_steps = 0

    if args.score_mode == "none":
        print("  score-mode=none: skipping execution.")
    elif not executor_model:
        print(
            "  ⚠️  No executor model set — cannot run headless. Pass EXECUTOR_MODEL=..."
        )
        _print_manual_fallback(args, executor_model, prompt_path, clipped)
        if args.score_mode == "manual":
            print("=" * 66)
            return 0
    elif has_run_cmd:
        if max_steps > 1:
            steps_dir.mkdir(exist_ok=True)

        for step_n in range(1, max_steps + 1):
            if max_steps > 1:
                if step_n == 1:
                    step_prompt_path = prompt_path
                else:
                    step_prompt_path = generate_continuation_prompt(
                        args.run, step_n, steps_dir
                    )
                    # Also copy step prompt to clipboard so user can paste if needed
                    copy_to_clipboard(step_prompt_path.read_text())
                step_log = steps_dir / f"step-{step_n}.log"
                print(f"\n  --- Step {step_n}/{max_steps} ---")
            else:
                step_prompt_path = prompt_path
                step_log = opencode_log

            prompt_text = step_prompt_path.read_text()
            cmd = build_opencode_cmd(executor_model, None, prompt_text)
            print(f"  Model:       {executor_model}")
            print(f"  Log:         {step_log.relative_to(REPO_ROOT)}")
            print("-" * 66)

            # For multi-step, each step log is primary; combined goes to opencode.log
            also_append = opencode_log if max_steps > 1 else None
            rc = run_opencode(cmd, step_log, also_append=also_append)
            completed_steps += 1

            print("-" * 66)
            if rc != 0:
                print(
                    f"  ⚠️  opencode exited with code {rc} — see {step_log.relative_to(REPO_ROOT)}"
                )
                break

            if max_steps > 1:
                step_text = step_log.read_text()
                if detect_stop_signal(step_text):
                    print(f"  Agent signaled completion at step {step_n}.")
                    break
                if step_n < max_steps:
                    print(
                        f"  Step {step_n} complete — continuing to step {step_n + 1}..."
                    )
            else:
                print("  opencode run finished.")
    else:
        print("  ⚠️  `opencode run` not available in this opencode version.")
        _print_manual_fallback(args, executor_model, prompt_path, clipped)
        if args.score_mode == "manual":
            print()
            print("  Workspace and prompt are ready. Exiting.")
            print("=" * 66)
            return 0

    # -----------------------------------------------------------------------
    # PHASE 3 — SCORE
    # -----------------------------------------------------------------------
    print()
    print("  [3/5] Scoring...")

    score_data: tuple[int, int, str] | None = None

    if args.score_mode == "none":
        print("  score-mode=none: skipping scoring.")
    elif args.score_mode == "manual":
        print("  Run is prepared. Score it manually, then:")
        print(f"    make bench-close RUN={args.run} SCORE=<0-10> INTERVENTIONS=<N>")
        # Still write usage.json before exiting
        print()
        print("  [4/5] Writing usage skeleton...")
        log_usage = parse_log_usage(opencode_log)
        usage = pull_usage_litellm(args.run) or pull_usage_langfuse(session_id) or {}
        if log_usage:
            usage = {**log_usage, **usage}
        write_usage_json(out_dir, usage or None)
        print("=" * 66)
        return 0
    else:  # interactive
        if args.score is not None and args.interventions is not None:
            # Score sanity check before accepting pre-supplied score
            if not score_sanity_check(
                WORKSPACE_ROOT, args.run, args.score, interactive=True
            ):
                print("  Aborting — re-run with corrected score.")
                return 1
            score_data = (args.score, args.interventions, args.notes)
        else:
            print()
            try:
                s = int(input("  Functional score (0-10): "))
                i = int(input("  Human interventions: "))
                n = input("  Notes (optional, Enter to skip): ").strip()
            except (ValueError, EOFError) as e:
                print(f"  ⚠️  Could not read score: {e}. Leaving run open.")
                print(
                    f"     Close manually: make bench-close RUN={args.run} SCORE=? INTERVENTIONS=?"
                )
                print("=" * 66)
                return 1

            if not score_sanity_check(WORKSPACE_ROOT, args.run, s, interactive=True):
                print("  Aborting — re-run with corrected score.")
                return 1

            score_data = (s, i, n)

    # -----------------------------------------------------------------------
    # PHASE 4 — ENRICH (before close so usage lands in report)
    # -----------------------------------------------------------------------
    print()
    print("  [4/5] Enriching with usage data...")

    log_usage = parse_log_usage(opencode_log)
    remote_usage = pull_usage_litellm(args.run) or pull_usage_langfuse(session_id)

    # Merge: remote values (real tokens/cost) take precedence over log estimates
    merged_usage: dict = {}
    if log_usage:
        merged_usage.update(log_usage)
    if remote_usage:
        merged_usage.update(remote_usage)
    if completed_steps > 1:
        merged_usage["steps"] = completed_steps

    usage_path = write_usage_json(out_dir, merged_usage or None)
    if merged_usage:
        keys = [k for k, v in merged_usage.items() if v is not None]
        print(f"  Usage fields populated: {keys}")
    else:
        print(
            f"  Usage not available (LITELLM_MASTER_KEY / LANGFUSE_SECRET_KEY not set "
            f"or no data yet). Fields left null in {usage_path.relative_to(REPO_ROOT)}."
        )

    # -----------------------------------------------------------------------
    # PHASE 5 — CLOSE
    # -----------------------------------------------------------------------
    if score_data:
        print()
        print("  [5/5] Closing run...")
        s, i, n = score_data
        close_run(args, run, runs_path, out_dir, s, i, n)
    else:
        print()
        print("  [5/5] No score available — run left open.")

    log_mlflow(args, run, score_data, merged_usage or None, out_dir)

    # -----------------------------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------------------------
    print()
    print("=" * 66)
    print(f"  bench-run complete: {args.run}")
    print("=" * 66)
    if score_data:
        s, i, n = score_data
        print(f"  Score:         {s}/10")
        print(f"  Interventions: {i}")
        if n:
            print(f"  Notes:         {n}")
    else:
        print("  Status:        prepared (score pending)")
    if completed_steps:
        print(f"  Steps run:     {completed_steps}")
    print(f"  Session ID:    {session_id}")
    print(f"  Artifacts:     data/runs/{args.run}/")

    print()
    print("  Next:")
    print("    make bench-results")
    next_run = find_next_run(runs_path, args.run)
    if next_run:
        nr_id = next_run["run_id"]
        nr_models = next_run.get("gateway_models", [])
        nr_em = nr_models[-1] if nr_models else executor_model
        nr_prompt = VARIANT_TO_SHORT.get(
            next_run.get("prompt_variant", ""), prompt_short
        )
        nr_pm = nr_models[0] if len(nr_models) > 1 else ""
        pm_part = f" PLANNER_MODEL={nr_pm}" if nr_pm and nr_pm != nr_em else ""
        steps_part = f" MAX_STEPS={max_steps}" if max_steps > 1 else ""
        print(
            f"    make bench-run CASE={args.case} RUN={nr_id}"
            f" PROMPT={nr_prompt} EXECUTOR_MODEL={nr_em}"
            f"{pm_part}{steps_part} SCORE_MODE=interactive"
        )

    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
