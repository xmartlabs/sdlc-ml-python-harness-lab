#!/usr/bin/env python3
"""Export a run's traces to Langfuse (one session per run, one trace per step).

Everything a trace needs is already in `data/runs/<run_id>/`, so this works on
runs that finished months ago as well as on the one that just completed.

    python scripts/export_langfuse_traces.py --run tetris-v1-K-061
    python scripts/export_langfuse_traces.py --all --limit 20
    python scripts/export_langfuse_traces.py --run <id> --dry-run
    python scripts/export_langfuse_traces.py --check

`--dry-run` builds and prints the payload without contacting Langfuse, and
works even when the integration is switched off — the way to inspect the trace
shape before sending anything anywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from harness_lab_app.observability.client import check_connection  # noqa: E402
from harness_lab_app.observability.config import LangfuseConfig  # noqa: E402
from harness_lab_app.observability.run_export import (  # noqa: E402
    build_run_payload,
    build_run_spans,
    export_run,
)

RUNS_DIR = REPO_ROOT / "data" / "runs"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--run", help="Run id (directory under data/runs/)")
    target.add_argument(
        "--all",
        action="store_true",
        help="Export every run directory found on disk (backfill)",
    )
    target.add_argument(
        "--check",
        action="store_true",
        help="Print the resolved config and verify the host and credentials",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the payload and print a summary; send nothing",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --dry-run, print the full OTLP payload instead of a summary",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="With --all, stop after N runs (0 = no limit)",
    )
    return parser.parse_args(argv)


def _summarize(run_dir: Path, config: LangfuseConfig) -> None:
    spans = build_run_spans(run_dir, config=config)
    traces = {span["traceId"] for span in spans}
    print(f"{run_dir.name}: {len(spans)} spans across {len(traces)} traces")
    print(f"  session: {config.session_id(run_dir.name)}")
    for span in spans:
        attrs = {a["key"]: a["value"] for a in span["attributes"]}
        input_len = len(
            attrs.get("langfuse.observation.input", {}).get("stringValue", "")
        )
        output_len = len(
            attrs.get("langfuse.observation.output", {}).get("stringValue", "")
        )
        start = int(span["startTimeUnixNano"])
        end = int(span["endTimeUnixNano"])
        print(
            f"  - {span['name']:<24} {(end - start) / 1e9:>8.1f}s "
            f"in={input_len:<7} out={output_len:<7} trace={span['traceId'][:8]}"
        )


def _dry_run(run_dir: Path, config: LangfuseConfig, as_json: bool) -> int:
    if as_json:
        print(json.dumps(build_run_payload(run_dir, config=config), indent=2))
    else:
        _summarize(run_dir, config)
    return 0


def _export(run_dir: Path, config: LangfuseConfig) -> int:
    result = export_run(run_dir, config=config)
    if result.ok:
        print(
            f"✅ {result.run_id}: {result.spans} spans / {result.traces} traces "
            f"→ session {result.session_id}"
        )
        if result.session_url:
            print(f"   {result.session_url}")
        return 0
    print(f"⚠️  {result.run_id}: {result.reason}")
    return 1


def _check(config: LangfuseConfig) -> int:
    print(f"host:        {config.host}")
    print(f"enabled:     {config.enabled}")
    print(f"environment: {config.environment}")
    print(f"auto-export: {config.auto_export}")
    print(f"session ids: {config.session_prefix}<run_id>")
    print(f"project url: {config.project_url or '(unset — no deep links)'}")
    if not config.enabled:
        print(f"⚠️  {config.disabled_reason}")
        return 1
    result = check_connection(config)
    print(("✅ " if result.ok else "❌ ") + result.reason)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = LangfuseConfig.from_env()

    if args.check:
        return _check(config)

    if not args.dry_run and not config.enabled:
        print(f"Langfuse export skipped — {config.disabled_reason}")
        print("Set LANGFUSE_ENABLED=true with both keys in .env, or use --dry-run.")
        return 1

    if args.run:
        run_dir = RUNS_DIR / args.run
        if not run_dir.is_dir():
            print(f"No run directory at {run_dir}")
            return 1
        targets = [run_dir]
    else:
        targets = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir())
        if args.limit > 0:
            targets = targets[: args.limit]

    failures = 0
    for run_dir in targets:
        if args.dry_run:
            failures += _dry_run(run_dir, config, args.json)
        else:
            failures += _export(run_dir, config)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
