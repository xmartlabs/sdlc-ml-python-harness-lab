#!/usr/bin/env python3
"""Close a benchmark run: save artifacts, update runs.yaml, commit.

Usage (prefer make):
    make bench-close RUN=tetris-v1-A-002 SCORE=5 INTERVENTIONS=0
    uv run python scripts/bench_close.py --run tetris-v1-A-002 --score 5 --interventions 0
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import datetime

from bench_start import (
    BENCH_CONTEXT,
    BENCH_CONTEXT_PLACEHOLDER,
    WORKSPACE_ROOT,
    restore_env_token,
)
from create_run import (
    DATA_DIR,
    REPO_ROOT,
    RUNS_FILES,
    _load_env,
    find_run,
    update_results_csv,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--run", required=True, help="run_id, e.g. tetris-v1-A-002")
    p.add_argument("--benchmark", default="tetris-v1")
    p.add_argument("--score", required=True, type=int, help="Functional score 0-10")
    p.add_argument(
        "--interventions", required=True, type=int, help="Human interventions count"
    )
    p.add_argument("--notes", default="", help="Optional notes (quoted string)")
    return p.parse_args(argv)


def update_runs_yaml(
    runs_path: Path, run_id: str, score: int, interventions: int, notes: str
) -> None:
    text = runs_path.read_text()
    rid = re.escape(run_id)

    # status: running -> completed
    text = re.sub(
        rf"(run_id: {rid}.*?status: )running",
        r"\1completed",
        text,
        count=1,
        flags=re.DOTALL,
    )
    # result_level: <empty or old> -> score
    text = re.sub(
        rf"(run_id: {rid}.*?result_level:)[ ]*\S*",
        rf"\g<1> {score}",
        text,
        count=1,
        flags=re.DOTALL,
    )
    # human_interventions: <empty or old> -> N
    text = re.sub(
        rf"(run_id: {rid}.*?human_interventions:)[ ]*\S*",
        rf"\g<1> {interventions}",
        text,
        count=1,
        flags=re.DOTALL,
    )
    # notes: "old" -> "new" (if provided)
    if notes:
        escaped = notes.replace("\\", "\\\\").replace('"', '\\"')
        text = re.sub(
            rf'(run_id: {rid}.*?notes: *)"[^"]*"',
            rf'\g<1>"{escaped}"',
            text,
            count=1,
            flags=re.DOTALL,
        )

    runs_path.write_text(text)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _load_env()

    if not (0 <= args.score <= 10):
        sys.exit("❌ SCORE must be between 0 and 10")
    if args.interventions < 0:
        sys.exit("❌ INTERVENTIONS must be >= 0")

    runs_path = RUNS_FILES.get(args.benchmark)
    if runs_path is None:
        sys.exit(f"Unknown benchmark '{args.benchmark}'")

    run = find_run(runs_path, args.run)
    if run.get("status") == "completed":
        sys.exit(
            f"Run {args.run} is already marked completed.\n"
            f"If you need to re-score it, edit runs.yaml manually."
        )

    workspace = WORKSPACE_ROOT / args.run
    if not workspace.exists():
        sys.exit(
            f"❌ workspace/{args.run}/ not found.\n"
            f"   If OpenCode wrote files to the repo root, move them first:\n"
            f"   mkdir -p workspace/{args.run} && mv <files> workspace/{args.run}/"
        )

    out_dir = DATA_DIR / args.run
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy workspace/ -> data/runs/<run-id>/workspace/
    dest = out_dir / "workspace"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(workspace, dest)
    file_count = sum(1 for _ in dest.rglob("*") if _.is_file())
    print(
        f"  Copied workspace/{args.run}/ -> data/runs/{args.run}/workspace/ ({file_count} files)"
    )

    # 2. Diff: compare /dev/null to the saved workspace (shows all new files)
    diff_path = out_dir / "diff.patch"
    result = _run_git(
        ["git", "diff", "--no-index", "/dev/null", str(dest)], cwd=REPO_ROOT
    )
    # git diff --no-index exits 1 when there are diffs (expected when files differ)
    diff_path.write_text(result.stdout)
    diff_lines = result.stdout.count("\n")
    print(f"  diff.patch: {diff_lines} lines")

    # 3. Remove active workspace
    shutil.rmtree(workspace)
    if WORKSPACE_ROOT.exists() and not any(WORKSPACE_ROOT.iterdir()):
        WORKSPACE_ROOT.rmdir()
    print(f"  Removed workspace/{args.run}/")

    # 4. Reset BENCH_CONTEXT.md
    BENCH_CONTEXT.write_text(BENCH_CONTEXT_PLACEHOLDER)
    print("  BENCH_CONTEXT.md reset")

    # 5. Restore .env token
    env_path = REPO_ROOT / ".env"
    restore_env_token(env_path)
    print("  .env GATEWAY_TOKEN restored")

    # 6. Update runs.yaml
    update_runs_yaml(runs_path, args.run, args.score, args.interventions, args.notes)
    session_id = run.get("langfuse_session_id", "")
    print(
        f"  runs.yaml: status=completed  score={args.score}/10  "
        f"interventions={args.interventions}"
    )

    # 6b. Update results.csv
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
            "prompt_variant": run.get("prompt_variant", ""),
            "planner_model": run.get("planner_model", ""),
            "executor_model": run.get("executor_model", ""),
            "executor_context": str(run.get("executor_context", "")),
            "temperature": str(run.get("temperature", "")),
            "functional_level": str(args.score),
            "human_interventions": str(args.interventions),
            "langfuse_session": run.get("langfuse_session_id", ""),
            "branch": run.get("branch", ""),
            "notes": args.notes,
        },
    )
    print("  results.csv updated")

    # 7. Commit
    files_to_stage = [
        str((out_dir).relative_to(REPO_ROOT)),
        str(runs_path.relative_to(REPO_ROOT)),
        str(results_path.relative_to(REPO_ROOT)),
        "BENCH_CONTEXT.md",
    ]
    _run_git(["git", "add", *files_to_stage], cwd=REPO_ROOT)

    notes_suffix = f", {args.notes}" if args.notes else ""
    commit_msg = (
        f"bench: close {args.run} "
        f"(score={args.score}/10, interventions={args.interventions}{notes_suffix})"
    )
    result = _run_git(["git", "commit", "-m", commit_msg], cwd=REPO_ROOT)
    if result.returncode == 0:
        sha = result.stdout.strip().split("\n")[0]
        print(f"  git commit: {sha}")
    else:
        print(
            f"  ⚠️  git commit skipped: {result.stderr.strip() or 'nothing to commit'}"
        )

    # 8. Summary
    print()
    print("=" * 66)
    print(f"  bench-close: {args.run} ✓")
    print("=" * 66)
    print(f"  Score:         {args.score}/10")
    print(f"  Interventions: {args.interventions}")
    if args.notes:
        print(f"  Notes:         {args.notes}")
    if session_id:
        print(f"  Langfuse:      session_id={session_id}")
    print(f"  Artifacts:     data/runs/{args.run}/")
    print()
    print("  Next run: pick the next run_id from runs.yaml and run bench-start.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
