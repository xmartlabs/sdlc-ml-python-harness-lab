#!/usr/bin/env python3
"""Print a live comparison table from benchmarks/tetris/runs.yaml.

Usage:
    make bench-results
    uv run python scripts/show_results.py [--benchmark tetris-v1]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML not found. Run: uv run python scripts/show_results.py")

REPO_ROOT = Path(__file__).parent.parent

RUNS_FILES = {
    "tetris-v1": REPO_ROOT / "benchmarks/tetris/runs.yaml",
}

SCORE_LABELS = {
    0: "no code",
    1: "doesn't run",
    2: "runs, no window",
    3: "window opens",
    4: "static piece",
    5: "piece falls",
    6: "lateral move",
    7: "rotation",
    8: "collision+lock",
    9: "lines+score",
    10: "fully playable",
}

STATUS_ICON = {
    "pending": "·",
    "running": "▶",
    "completed": "✓",
}


def fmt_score(val) -> str:
    if val is None or val == "":
        return "  —  "
    n = int(val)
    label = SCORE_LABELS.get(n, "")
    return f"{n:2d}/10  ({label})"


def fmt_int(val) -> str:
    if val is None or val == "":
        return "—"
    return str(int(val))


def cell_letter(run_id: str) -> str:
    parts = run_id.split("-")
    return parts[-2] if len(parts) >= 2 else "?"


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--benchmark", default="tetris-v1")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    runs_path = RUNS_FILES.get(args.benchmark)
    if runs_path is None or not runs_path.exists():
        sys.exit(f"runs.yaml not found for benchmark '{args.benchmark}'")

    data = yaml.safe_load(runs_path.read_text())
    runs = data.get("runs", [])

    base = data.get("base_commit", "TBD")[:8]
    bench_id = data.get("benchmark_id", args.benchmark)

    print()
    print(f"  {bench_id} — Comparison results")
    print(f"  base_commit: {base}")
    print()
    print(
        f"  {'Cell':4}  {'Methodology':22}  {'Models':38}  "
        f"{'Run':5}  {'Score':18}  {'Int':3}  {'Status':8}  Notes"
    )
    print("  " + "-" * 115)

    current_cell = None
    cell_scores: list[int] = []
    cell_runs_done = 0
    cell_runs_total = 0

    def print_cell_avg(cell: str, scores: list[int], done: int, total: int) -> None:
        if not cell:
            return
        completed_scores = [s for s in scores if s is not None]
        if completed_scores:
            avg = sum(completed_scores) / len(completed_scores)
            print(
                f"  {'':4}  {'':22}  {'':38}  {'':5}  "
                f"  ↳ Cell {cell} avg: {avg:.1f}/10 over {done}/{total} completed runs"
            )
        print()

    for run in runs:
        run_id = run["run_id"]
        cell = cell_letter(run_id)
        rep = run_id.split("-")[-1]

        if cell != current_cell:
            print_cell_avg(current_cell, cell_scores, cell_runs_done, cell_runs_total)
            current_cell = cell
            cell_scores = []
            cell_runs_done = 0
            cell_runs_total = 0

        cell_runs_total += 1

        status = run.get("status", "pending")
        score = run.get("result_level")
        interventions = run.get("human_interventions")
        methodology = run.get("methodology", "")
        models = run.get("gateway_models", [])
        # Shorten model names for display
        short_models = ", ".join(
            m.replace("gateway/lmstudio-", "")
            .replace("gateway/", "")
            .replace("-build", "")
            for m in models
        )
        notes = run.get("notes", "") or ""
        notes = notes[:30] + "…" if len(notes) > 30 else notes
        icon = STATUS_ICON.get(status, "?")

        if status == "completed":
            cell_runs_done += 1
            cell_scores.append(int(score) if score not in (None, "") else None)

        print(
            f"  {cell:4}  {methodology:22}  {short_models:38}  "
            f"#{rep:4}  {fmt_score(score):18}  {fmt_int(interventions):3}  "
            f"{icon} {status:6}  {notes}"
        )

    # last cell
    print_cell_avg(current_cell, cell_scores, cell_runs_done, cell_runs_total)

    # Summary
    all_completed = [r for r in runs if r.get("status") == "completed"]
    all_running = [r for r in runs if r.get("status") == "running"]
    all_pending = [r for r in runs if r.get("status") == "pending"]

    print(
        f"  Status: {len(all_completed)} completed  "
        f"{len(all_running)} running  "
        f"{len(all_pending)} pending  "
        f"(total: {len(runs)})"
    )
    print()


if __name__ == "__main__":
    sys.exit(main())
