#!/usr/bin/env python3
"""Unified benchmark runner: prepare → execute → score → close.

This is the CLI backend a future web UI would call. It replaces the manual
bench-start / paste / bench-close flow with a single reproducible command.

Usage (prefer make):
    make bench-run CASE=tetris-v1 RUN=tetris-v1-A-003 PROMPT=incremental \\
        EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build SCORE_MODE=manual

    make bench-run ... DRY_RUN=1   # see what would happen without writing anything

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

# Non-interactive execution uses the `opencode run [message..]` subcommand
# (NOT a top-level flag). See `opencode run --help`.


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


# ---------------------------------------------------------------------------
# Execution detection
# ---------------------------------------------------------------------------


def detect_run_subcommand() -> bool:
    """Return True if `opencode run` (headless, non-interactive) is available.

    Non-interactive execution is the `opencode run [message..]` subcommand, not a
    top-level flag. We verify by invoking its --help and confirming it parses.
    """
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

    --dangerously-skip-permissions is required for headless runs so the agent does
    not block waiting for interactive permission approvals.
    """
    cmd = ["opencode", "run", "--dangerously-skip-permissions", "--print-logs"]
    if executor_model:
        cmd += ["-m", executor_model]
    if agent:
        cmd += ["--agent", agent]
    cmd.append(message)
    return cmd


def _print_manual_fallback(
    args, executor_model: str, prompt_path: Path, clipped: bool
) -> None:
    """Precise manual instructions when headless `opencode run` is unavailable."""
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


def run_opencode(cmd: list[str], log_path: Path) -> int:
    """Run opencode, streaming output live to the terminal and capturing to log_path."""
    import datetime as _dt

    with log_path.open("w") as log:
        header = f"# opencode run started {_dt.datetime.utcnow().isoformat()}Z\n"
        # Log the command WITHOUT the (large, last) message argument
        log.write(header + "# cmd: " + " ".join(cmd[:-1]) + " <prompt>\n\n")
        log.flush()
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
        proc.wait()
    return proc.returncode


# ---------------------------------------------------------------------------
# Usage enrichment (optional — fail silently)
# ---------------------------------------------------------------------------


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
        # Normalize whatever LiteLLM returns
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
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "time_minutes": None,
        "files_modified": [],
        "diff_lines": None,
    }
    if usage:
        skeleton.update(usage)
    path = out_dir / "usage.json"
    path.write_text(json.dumps(skeleton, indent=2))
    return path


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

    if not workspace.exists():
        print(
            f"  ⚠️  workspace/{args.run}/ not found — skipping artifact copy.\n"
            f"     If files were written to the repo root, move them first:\n"
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
        print(f"  diff.patch: {result.stdout.count(chr(10))} lines")

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
        print(f"    workspace/{args.run}/")
        print()
        if detect_run_subcommand():
            preview = build_opencode_cmd(executor_model, None, "<prompt>")
            print("  Execution: " + " ".join(preview))
        else:
            print("  Execution: `opencode run` not available — manual paste required")
        print()
        print("  LiteLLM key metadata that would be registered:")
        print(f"    key_alias: {args.run}")
        print(f"    metadata.session_id: {session_id}")
        print(f"    metadata.run_id: {args.run}")
        print(f"    metadata.benchmark_id: {args.case}")
        print(f"    metadata.executor_model: {executor_model}")
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
    (WORKSPACE_ROOT / args.run).mkdir(parents=True, exist_ok=True)

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

    # Virtual key
    run_key: str | None = None
    if args.create_litellm_key:
        gateway_base = os.environ.get("GATEWAY_BASE_URL", "")
        master_key = os.environ.get("LITELLM_MASTER_KEY", "")
        if not gateway_base or not master_key:
            print(
                f"  ⚠️  LiteLLM key skipped: GATEWAY_BASE_URL or LITELLM_MASTER_KEY not set.\n"
                f"     Create key manually → key_alias={args.run}  "
                f"metadata.session_id={session_id}"
            )
        else:
            try:
                run_key = create_litellm_key(gateway_base, master_key, run)
                key_file = out_dir / ".gateway_key"
                key_file.write_text(run_key)
                key_file.chmod(0o600)
                env_path = REPO_ROOT / ".env"
                patch_env_token(env_path, run_key)
                os.environ["GATEWAY_TOKEN"] = run_key
                print(
                    f"  Key:         written to {key_file.relative_to(REPO_ROOT)} (gitignored)"
                )
            except RuntimeError as e:
                print(f"  ⚠️  LiteLLM key creation failed: {e}")

    clipped = copy_to_clipboard(prompt_path.read_text())

    # -----------------------------------------------------------------------
    # PHASE 2 — EXECUTE
    # -----------------------------------------------------------------------
    print()
    print("  [2/5] Executing...")

    opencode_log = out_dir / "opencode.log"
    has_run_cmd = detect_run_subcommand()

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
        cmd = build_opencode_cmd(executor_model, None, prompt_path.read_text())
        print(f"  Model:       {executor_model}")
        print(
            f"  Command:     opencode run --dangerously-skip-permissions -m {executor_model} <prompt>"
        )
        print(f"  Log:         {opencode_log.relative_to(REPO_ROOT)}")
        print("-" * 66)
        rc = run_opencode(cmd, opencode_log)
        print("-" * 66)
        if rc != 0:
            print(
                f"  ⚠️  opencode exited with code {rc} — see {opencode_log.relative_to(REPO_ROOT)}"
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
        print("=" * 66)
        return 0
    else:  # interactive
        if args.score is not None and args.interventions is not None:
            score_data = (args.score, args.interventions, args.notes)
        else:
            print()
            try:
                s = int(input("  Functional score (0-10): "))
                i = int(input("  Human interventions: "))
                n = input("  Notes (optional, Enter to skip): ").strip()
                score_data = (s, i, n)
            except (ValueError, EOFError) as e:
                print(f"  ⚠️  Could not read score: {e}. Leaving run open.")
                print(
                    f"     Close manually: make bench-close RUN={args.run} SCORE=? INTERVENTIONS=?"
                )
                print("=" * 66)
                return 1

    # -----------------------------------------------------------------------
    # PHASE 5 — ENRICH (before close so usage lands in report)
    # -----------------------------------------------------------------------
    print()
    print("  [4/5] Enriching with usage data...")

    usage = pull_usage_litellm(args.run) or pull_usage_langfuse(session_id)
    usage_path = write_usage_json(out_dir, usage)
    if usage:
        print(f"  Usage pulled: {usage}")
    else:
        print(
            f"  Usage not available (LITELLM_MASTER_KEY / LANGFUSE_SECRET_KEY not set "
            f"or no data yet). Fields left null in {usage_path.relative_to(REPO_ROOT)}."
        )

    # -----------------------------------------------------------------------
    # PHASE 4 — CLOSE
    # -----------------------------------------------------------------------
    if score_data:
        print()
        print("  [5/5] Closing run...")
        s, i, n = score_data
        close_run(args, run, runs_path, out_dir, s, i, n)
    else:
        print()
        print("  [5/5] No score available — run left open.")

    log_mlflow(args, run, score_data, usage, out_dir)

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
    print(f"  Session ID:    {session_id}")
    print(f"  Artifacts:     data/runs/{args.run}/")
    print("  Next:          make bench-results")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
