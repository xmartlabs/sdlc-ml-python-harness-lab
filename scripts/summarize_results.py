#!/usr/bin/env python3
"""Aggregate completed runs into the comparison table.

STUB: argument parsing is real; the join across runs.yaml + data/exports + manual scores is TODO.

Responsibilities (when implemented):
- Read runs.yaml (status, models, scores) and data/exports/*.json (usage).
- Emit the target table to reports/<date>-<benchmark>.md:
    Methodology | Model Plan | Model Exec | Cost | Tokens | Time | Turns | Score | Human Int.
- Aggregate the 3 repetitions per cell (median + spread) so stability is visible.

Usage:
    python scripts/summarize_results.py --benchmark tetris-v1
"""

from __future__ import annotations

import argparse
import sys

TABLE_COLUMNS = (
    "Methodology",
    "Model Plan",
    "Model Exec",
    "Cost",
    "Tokens",
    "Time",
    "Turns",
    "Score",
    "Human Int.",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--benchmark", default="tetris-v1", help="benchmark_id (default: tetris-v1)"
    )
    parser.add_argument(
        "--runs-file", default="benchmarks/tetris/runs.yaml", help="runs registry path"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"[summarize_results] benchmark={args.benchmark} runs_file={args.runs_file}")
    print(f"[summarize_results] columns: {' | '.join(TABLE_COLUMNS)}")
    print(
        "[summarize_results] TODO: join runs.yaml + data/exports + scores -> reports/<date>.md"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
