#!/usr/bin/env python3
"""Capture manual Tetris scoring for a run and write it into the run report.

STUB: argument parsing is real; the interactive prompts, report write-back and Langfuse score
push are TODO. Manual scoring first — see benchmarks/tetris/scoring.md.

Responsibilities (when implemented):
- Prompt for the functional level (0-10) and the functional checklist.
- Prompt for engineering metrics (files changed, diff size, scope 0-5, human interventions).
- Fill data/runs/<run-id>/report.md from templates/run-report-template.md.
- Push Langfuse scores against the run's session_id:
    tetris_functional_level (0-10), runnable (0/1), human_interventions (int),
    scope_adherence (0-5).

Usage:
    python scripts/score_tetris.py --run tetris-v1-A-001
"""

from __future__ import annotations

import argparse
import sys

LANGFUSE_SCORES = (
    "tetris_functional_level",
    "runnable",
    "human_interventions",
    "scope_adherence",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run", required=True, help="run_id, e.g. tetris-v1-A-001")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="reserved: read values from flags instead of prompts",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"[score_tetris] run={args.run}")
    print(
        f"[score_tetris] would push Langfuse scores to session_id=bench-{args.run}: {', '.join(LANGFUSE_SCORES)}"
    )
    print(
        "[score_tetris] TODO: interactive scoring + report write-back + Langfuse push"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
