#!/usr/bin/env python3
"""Register a benchmark run and scaffold its data directory.

Run with: uv run python scripts/create_run.py --benchmark tetris-v1 --run tetris-v1-A-001

What it does:
- Finds the run in benchmarks/<task>/runs.yaml and sets status -> running.
- Creates data/runs/<run-id>/ with a pre-filled report.md.
- Prints the session_id, branch, tags and .env snippet ready to paste.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML not found. Run with: uv run python scripts/create_run.py ...")

REPO_ROOT = Path(__file__).parent.parent
RUNS_FILES = {
    "tetris-v1": REPO_ROOT / "benchmarks/tetris/runs.yaml",
}
REPORT_TEMPLATE = REPO_ROOT / "templates/run-report-template.md"
DATA_DIR = REPO_ROOT / "data/runs"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--benchmark", required=True, help="benchmark_id, e.g. tetris-v1")
    parser.add_argument("--run", required=True, help="run_id, e.g. tetris-v1-A-001")
    return parser.parse_args(argv)


def find_run(runs_path: Path, run_id: str) -> dict:
    data = yaml.safe_load(runs_path.read_text())
    for run in data.get("runs", []):
        if run["run_id"] == run_id:
            return run
    available = [r["run_id"] for r in data.get("runs", [])]
    sys.exit(f"run_id '{run_id}' not found.\nAvailable:\n" + "\n".join(f"  {r}" for r in available))


def set_status_running(runs_path: Path, run_id: str) -> None:
    text = runs_path.read_text()
    pattern = rf"(run_id: {re.escape(run_id)}.*?status: )pending"
    new_text = re.sub(pattern, r"\1running", text, count=1, flags=re.DOTALL)
    if new_text == text:
        print(f"  note: status was not 'pending' for {run_id} — no change written to runs.yaml")
        return
    runs_path.write_text(new_text)


def scaffold_report(run: dict, out_dir: Path) -> Path:
    report = REPORT_TEMPLATE.read_text()
    tags = (
        f"benchmark:tetris-v1 harness:{run.get('harness','')} "
        f"method:{run.get('methodology','')} planner:{run.get('planner_model','')} "
        f"executor:{run.get('executor_model','')} prompt:{run.get('prompt_variant','')}"
    )
    replacements = {
        "<run-id>": run["run_id"],
        "- Harness:": f"- Harness: {run.get('harness', '')}",
        "- Methodology:": f"- Methodology: {run.get('methodology', '')}",
        "- Planner model:": f"- Planner model: {run.get('planner_model', '')}",
        "- Executor model:": f"- Executor model: {run.get('executor_model', '')}",
        "- Executor context:": f"- Executor context: {run.get('executor_context', '')}",
        "- Temperature:": f"- Temperature: {run.get('temperature', '')}",
        "- Prompt variant:": f"- Prompt variant: {run.get('prompt_variant', '')}",
        "- Session ID:": f"- Session ID: {run.get('langfuse_session_id', '')}",
        "- Tags:": f"- Tags: {tags}",
    }
    for old, new in replacements.items():
        report = report.replace(old, new, 1)
    report_path = out_dir / "report.md"
    report_path.write_text(report)
    return report_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    runs_path = RUNS_FILES.get(args.benchmark)
    if runs_path is None:
        sys.exit(f"Unknown benchmark '{args.benchmark}'. Known: {list(RUNS_FILES)}")
    if not runs_path.exists():
        sys.exit(f"runs.yaml not found: {runs_path}")

    run = find_run(runs_path, args.run)

    if run.get("status") == "completed":
        sys.exit(f"Run {args.run} is already completed. Create a new run_id.")

    out_dir = DATA_DIR / args.run
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = scaffold_report(run, out_dir)
    set_status_running(runs_path, args.run)

    session_id = run.get("langfuse_session_id", f"bench-{args.run}")
    branch = run.get("branch", f"bench/{args.run}")
    virtual_key = run.get("virtual_key", "bench-opencode-local")
    models = run.get("gateway_models", [])
    planner = run.get("planner_model", "")
    executor = run.get("executor_model", "")

    print()
    print("=" * 62)
    print(f"  Run registered: {args.run}")
    print("=" * 62)
    print(f"  Report:      {report_path.relative_to(REPO_ROOT)}")
    print(f"  Session ID:  {session_id}")
    print(f"  Branch:      {branch}")
    print(f"  Models:      {', '.join(models)}")
    print()
    print("  .env snippet (paste into the subject worktree):")
    print(f"    OPENCODE_MODEL={models[-1] if models else ''}")
    if planner != executor:
        print(f"    OPENCODE_MODEL_PLAN={models[0] if models else ''}")
    else:
        print(f"    OPENCODE_MODEL_PLAN={models[0] if models else ''}   # same model (local-only)")
    print(f"    GATEWAY_TOKEN=<your virtual key: {virtual_key}>")
    print()
    print("  Langfuse metadata to attach per call:")
    print(f"    session_id: {session_id}")
    print(f"    tags: benchmark:tetris-v1 method:{run.get('methodology','')} prompt:{run.get('prompt_variant','')}")
    print()
    print("  Next: follow docs/runbook-first-run.md")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
