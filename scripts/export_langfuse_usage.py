#!/usr/bin/env python3
"""Export token/cost/latency usage for a run from Langfuse/LiteLLM.

STUB: argument parsing is real; the Langfuse API calls are TODO. Implement this FIRST when
moving from manual to automated, since it removes the most tedious manual step.

Responsibilities (when implemented):
- Query Langfuse for all traces with the given session_id.
- Aggregate: requests, input tokens, output tokens, USD cost, wall time, turns, per-model split,
  and per-agent_role split (planner/executor/critic/tester) when that metadata is present.
- Write data/exports/<session_id>.json (and a flat CSV row) for summarize_results.py.

Auth comes from env (self-hosted Langfuse): LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY.

Usage:
    python scripts/export_langfuse_usage.py --session bench-tetris-v1-A-001
"""

from __future__ import annotations

import argparse
import sys


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--session",
        required=True,
        help="langfuse_session_id, e.g. bench-tetris-v1-A-001",
    )
    parser.add_argument(
        "--out-dir",
        default="data/exports",
        help="export directory (default: data/exports)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"[export_langfuse_usage] session={args.session} out_dir={args.out_dir}")
    print(
        "[export_langfuse_usage] would aggregate tokens/cost/latency + per-role split"
    )
    print(
        "[export_langfuse_usage] TODO: Langfuse query via LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
