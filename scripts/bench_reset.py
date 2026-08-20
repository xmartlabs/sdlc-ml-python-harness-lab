#!/usr/bin/env python3
"""Reset benchmark runs back to pending so they can be re-run.

Usage (prefer make):
    make bench-reset RUN=tetris-v1-A-003          # reset one run to pending
    make bench-reset CASE=tetris-v1               # reset ALL runs in a case to pending
    make bench-reset RUN=tetris-v1-A-003 CLEAN=1  # also remove data/runs/<run_id>/ and workspace/
    make bench-reset CASE=tetris-v1 CLEAN=1       # nuclear: reset all + wipe all artifacts

What it does:
  1. Sets status → pending, clears result_level and human_interventions in runs.yaml.
  2. Removes the run's row(s) from results.csv.
  3. If --clean: removes data/runs/<run_id>/ and workspace/<run_id>/ directories.

Asks for confirmation before destructive actions unless --force is passed.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_run import DATA_DIR, REPO_ROOT, RUNS_FILES, _load_env

try:
    import yaml
except ImportError:
    sys.exit("PyYAML not found. Run with: uv run python scripts/bench_reset.py ...")

WORKSPACE_ROOT = REPO_ROOT / "workspace"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", help="Single run_id to reset, e.g. tetris-v1-A-003")
    group.add_argument("--case", help="Reset ALL runs in a case, e.g. tetris-v1")
    p.add_argument(
        "--clean",
        action="store_true",
        help="Also remove data/runs/<run_id>/ and workspace/<run_id>/ directories",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompts (use with care)",
    )
    return p.parse_args(argv)


def _confirm(prompt: str, force: bool) -> bool:
    if force:
        return True
    try:
        ans = input(f"{prompt} [y/N]: ").strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def reset_run_in_yaml(runs_path: Path, run_id: str) -> bool:
    """Set status=pending, clear result_level and human_interventions for one run_id."""
    text = runs_path.read_text()
    rid = re.escape(run_id)

    # status: completed|running -> pending
    new = re.sub(
        rf"(run_id: {rid}.*?status: )(completed|running)",
        r"\1pending",
        text,
        count=1,
        flags=re.DOTALL,
    )
    # result_level: <value> -> (empty)
    new = re.sub(
        rf"(run_id: {rid}.*?result_level:)[ ]*\S*",
        r"\g<1>",
        new,
        count=1,
        flags=re.DOTALL,
    )
    # human_interventions: <value> -> (empty)
    new = re.sub(
        rf"(run_id: {rid}.*?human_interventions:)[ ]*\S*",
        r"\g<1>",
        new,
        count=1,
        flags=re.DOTALL,
    )
    if new == text:
        return False
    runs_path.write_text(new)
    return True


def reset_all_runs_in_yaml(runs_path: Path) -> int:
    """Reset all runs in a yaml file. Returns count of runs reset."""
    data = yaml.safe_load(runs_path.read_text())
    count = 0
    for run in data.get("runs", []):
        if run.get("status") in ("completed", "running"):
            count += 1
        run["status"] = "pending"
        run["result_level"] = None
        run["human_interventions"] = None
    text = yaml.dump(
        data, allow_unicode=True, default_flow_style=False, sort_keys=False
    )
    runs_path.write_text(text)
    return count


def remove_from_csv(results_path: Path, run_id: str) -> bool:
    if not results_path.exists():
        return False
    with results_path.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = [r for r in reader if r.get("run_id") != run_id]

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    results_path.write_text(out.getvalue())
    return True


def clear_csv(results_path: Path) -> None:
    if not results_path.exists():
        return
    with results_path.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    results_path.write_text(out.getvalue())


def clean_artifacts(run_id: str) -> list[str]:
    """Remove data/runs/<run_id>/ and workspace/<run_id>/. Returns list of removed paths."""
    removed = []
    for target in [DATA_DIR / run_id, WORKSPACE_ROOT / run_id]:
        if target.exists():
            shutil.rmtree(target)
            removed.append(str(target.relative_to(REPO_ROOT)))
    return removed


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _load_env()

    # ---- Single run reset -----------------------------------------------
    if args.run:
        run_id = args.run
        case = run_id.rsplit("-", 2)[0]  # "tetris-v1-A-003" → "tetris-v1"
        runs_path = RUNS_FILES.get(case)
        if runs_path is None:
            sys.exit(
                f"Cannot determine case for run '{run_id}'. Known cases: {list(RUNS_FILES)}"
            )

        print()
        print("=" * 66)
        print(f"  bench-reset: {run_id}")
        print("=" * 66)
        print(f"  runs.yaml:   {runs_path.relative_to(REPO_ROOT)}")
        if args.clean:
            print(
                f"  CLEAN mode:  will also remove data/runs/{run_id}/ and workspace/{run_id}/"
            )
        print()

        if not _confirm(f"  Reset {run_id} to pending?", args.force):
            print("  Cancelled.")
            return 0

        changed = reset_run_in_yaml(runs_path, run_id)
        if changed:
            print(f"  runs.yaml: {run_id} → status=pending, result_level cleared")
        else:
            print(
                f"  runs.yaml: {run_id} was already pending (or not found) — no change"
            )

        results_path = runs_path.parent / "results.csv"
        remove_from_csv(results_path, run_id)
        print(f"  results.csv: row for {run_id} removed (if present)")

        if args.clean:
            removed = clean_artifacts(run_id)
            if removed:
                for p in removed:
                    print(f"  removed:     {p}/")
            else:
                print(
                    f"  clean:       nothing to remove (data/runs/{run_id}/ and workspace/{run_id}/ not found)"
                )

    # ---- Full case reset ------------------------------------------------
    else:
        case = args.case
        runs_path = RUNS_FILES.get(case)
        if runs_path is None:
            sys.exit(f"Unknown case '{case}'. Known cases: {list(RUNS_FILES)}")

        data = yaml.safe_load(runs_path.read_text())
        all_runs = [r["run_id"] for r in data.get("runs", [])]
        n = len(all_runs)

        print()
        print("=" * 66)
        print(f"  bench-reset ALL: {case}")
        print("=" * 66)
        print(f"  Runs to reset:  {n}")
        if args.clean:
            print(
                "  CLEAN mode:     will also remove data/runs/<run_id>/ and workspace/<run_id>/ for ALL runs"
            )
        print()

        if not _confirm(
            f"  Reset ALL {n} runs in {case} to pending?"
            + (" + wipe artifacts?" if args.clean else ""),
            args.force,
        ):
            print("  Cancelled.")
            return 0

        count = reset_all_runs_in_yaml(runs_path)
        print(f"  runs.yaml: {count} run(s) reset to pending")

        results_path = runs_path.parent / "results.csv"
        clear_csv(results_path)
        print("  results.csv: all rows cleared (header preserved)")

        if args.clean:
            total_removed = []
            for run_id in all_runs:
                total_removed.extend(clean_artifacts(run_id))
            if total_removed:
                print(f"  removed:    {len(total_removed)} directories")
                for p in total_removed:
                    print(f"              {p}/")
            else:
                print("  clean:      no artifact directories found")

    print()
    print("  Done. Run `make bench-results` to confirm.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
