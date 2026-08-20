#!/usr/bin/env python3
"""Register a benchmark run and scaffold its data directory.

Run with: uv run python scripts/create_run.py --benchmark tetris-v1 --run tetris-v1-A-001

What it does:
1. Finds the run in benchmarks/<task>/runs.yaml, sets status -> running.
2. Creates data/runs/<run-id>/ with a pre-filled report.md.
3. Calls the LiteLLM admin API to create a run-specific virtual key that carries
   the session_id and tags baked in — so every OpenCode call is automatically
   attributed to this run in Langfuse without any changes to OpenCode itself.
4. Prints the generated key as GATEWAY_TOKEN ready to paste into the worktree .env.

Requires env vars (can live in .env at repo root):
  GATEWAY_BASE_URL    e.g. http://localhost:4000/v1
  LITELLM_MASTER_KEY  your LiteLLM master key (sk-...), used only for key creation
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
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
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--benchmark", required=True, help="benchmark_id, e.g. tetris-v1"
    )
    parser.add_argument("--run", required=True, help="run_id, e.g. tetris-v1-A-001")
    parser.add_argument(
        "--no-key",
        action="store_true",
        help="skip virtual key creation (print manual instructions instead)",
    )
    return parser.parse_args(argv)


def _load_env() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def find_run(runs_path: Path, run_id: str) -> dict:
    data = yaml.safe_load(runs_path.read_text())
    for run in data.get("runs", []):
        if run["run_id"] == run_id:
            return run
    available = [r["run_id"] for r in data.get("runs", [])]
    sys.exit(
        f"run_id '{run_id}' not found.\nAvailable:\n"
        + "\n".join(f"  {r}" for r in available)
    )


def set_status_running(runs_path: Path, run_id: str) -> None:
    text = runs_path.read_text()
    pattern = rf"(run_id: {re.escape(run_id)}.*?status: )pending"
    new_text = re.sub(pattern, r"\1running", text, count=1, flags=re.DOTALL)
    if new_text == text:
        print("  note: status was not 'pending' — no change written to runs.yaml")
        return
    runs_path.write_text(new_text)


def scaffold_report(run: dict, out_dir: Path) -> Path:
    report = REPORT_TEMPLATE.read_text()
    tags = (
        f"benchmark:tetris-v1 harness:{run.get('harness', '')} "
        f"method:{run.get('methodology', '')} planner:{run.get('planner_model', '')} "
        f"executor:{run.get('executor_model', '')} prompt:{run.get('prompt_variant', '')}"
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


def create_litellm_key(gateway_base: str, master_key: str, run: dict) -> str:
    """POST /key/generate to create a run-specific key with session_id + tags baked in.

    LiteLLM attaches the key's metadata and tags to every trace it forwards to Langfuse,
    so all OpenCode calls made with this key land in the correct Langfuse session
    automatically — no changes to OpenCode needed.
    """
    # Strip /v1 suffix — admin endpoints live at the root
    base = gateway_base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]

    parsed_base = urllib.parse.urlparse(base)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
        raise RuntimeError(
            "GATEWAY_BASE_URL must use http(s) and include a host, "
            f"got: {gateway_base!r}"
        )

    url = f"{base}/key/generate"

    session_id = run["langfuse_session_id"]

    # Note: LiteLLM `tags` require Enterprise. We use metadata only (free tier).
    # LiteLLM forwards metadata.session_id to Langfuse for session grouping.
    payload = {
        "key_alias": run["run_id"],
        "metadata": {
            "session_id": session_id,
            "langfuse_session_id": session_id,
            "run_id": run["run_id"],
            "benchmark_id": "tetris-v1",
            "methodology": run.get("methodology", ""),
            "planner_model": run.get("planner_model", ""),
            "executor_model": run.get("executor_model", ""),
            "prompt_variant": run.get("prompt_variant", ""),
        },
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json",
            # Cloudflare bot-protection requires browser-like headers on external gateways
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Origin": url.rsplit("/key", 1)[0],
        },
    )
    try:
        # URL is derived from validated GATEWAY_BASE_URL (http/https only).
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            result = json.loads(resp.read())
            return result["key"]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"LiteLLM key generation failed ({e.code}): {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach gateway at {url}: {e.reason}") from e


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _load_env()

    runs_path = RUNS_FILES.get(args.benchmark)
    if runs_path is None:
        sys.exit(f"Unknown benchmark '{args.benchmark}'. Known: {list(RUNS_FILES)}")

    run = find_run(runs_path, args.run)

    if run.get("status") == "completed":
        sys.exit(f"Run {args.run} is already completed. Use a new run_id.")

    out_dir = DATA_DIR / args.run
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = scaffold_report(run, out_dir)
    set_status_running(runs_path, args.run)

    session_id = run["langfuse_session_id"]
    branch = run.get("branch", f"bench/{args.run}")
    models = run.get("gateway_models", [])
    planner = run.get("planner_model", "")
    executor = run.get("executor_model", "")

    # --- Virtual key creation ------------------------------------------------
    run_key: str | None = None
    key_error: str | None = None

    if not args.no_key:
        gateway_base = os.environ.get("GATEWAY_BASE_URL", "")
        master_key = os.environ.get("LITELLM_MASTER_KEY", "")
        if not gateway_base:
            key_error = "GATEWAY_BASE_URL not set"
        elif not master_key:
            key_error = "LITELLM_MASTER_KEY not set (needed for key creation)"
        else:
            try:
                run_key = create_litellm_key(gateway_base, master_key, run)
            except RuntimeError as e:
                key_error = str(e)

    # --- Output --------------------------------------------------------------
    print()
    print("=" * 66)
    print(f"  Run registered: {args.run}")
    print("=" * 66)
    print(f"  Report:      {report_path.relative_to(REPO_ROOT)}")
    print(f"  Session ID:  {session_id}   (Langfuse session)")
    print(f"  Branch:      {branch}")
    print(f"  Models:      {', '.join(models)}")
    print()

    if run_key:
        print("  Virtual key created — session_id + tags baked in:")
        print(f"  GATEWAY_TOKEN={run_key}")
        print()
        print("  .env snippet for the subject worktree:")
        print(f"    OPENCODE_MODEL={models[-1] if models else ''}")
        if planner != executor:
            print(f"    OPENCODE_MODEL_PLAN={models[0] if models else ''}")
        else:
            print(
                f"    OPENCODE_MODEL_PLAN={models[0] if models else ''}   # same model (local-only)"
            )
        print(f"    GATEWAY_TOKEN={run_key}")
    else:
        tag_label = run.get("virtual_key", "bench-opencode-local")
        print(f"  Key creation skipped: {key_error or '--no-key'}")
        print("  Create the key manually in the LiteLLM UI or via:")
        print(f"    POST /key/generate  key_alias={args.run}")
        print(f"    metadata.session_id={session_id}")
        print(
            f"    tags=[benchmark:tetris-v1, method:{run.get('methodology', '')}, ...]"
        )
        print()
        print("  Or re-run after setting LITELLM_MASTER_KEY in .env")
        print()
        print("  .env snippet (use the key_alias until you have the real key):")
        print(f"    OPENCODE_MODEL={models[-1] if models else ''}")
        print(f"    OPENCODE_MODEL_PLAN={models[0] if models else ''}")
        print(f"    GATEWAY_TOKEN=<key for alias: {tag_label}>")

    print()
    print("  Next: docs/runbook-first-run.md  (Step 3 — git worktree)")
    print("=" * 66)
    return 0


def update_results_csv(results_path: Path, row: dict) -> None:
    """Append or update a row in results.csv keyed on run_id.

    Preserves all existing rows. Creates the file with the canonical header if absent.
    Only overwrites fields that are non-empty in the incoming row — existing values
    (e.g. tokens pulled from Langfuse) are not clobbered by a partial row.
    """
    import csv
    import io

    COLUMNS = [
        "run_id",
        "cell_id",
        "date",
        "methodology",
        "harness",
        "prompt_variant",
        "planner_model",
        "executor_model",
        "executor_context",
        "temperature",
        "requests",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "time_minutes",
        "turns",
        "files_modified",
        "diff_lines",
        "functional_level",
        "engineering_score",
        "reproducibility_score",
        "human_interventions",
        "langfuse_session",
        "branch",
        "notes",
    ]

    rows: list[dict] = []
    if results_path.exists():
        with results_path.open(newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

    updated = False
    incoming = {k: ("" if v is None else str(v)) for k, v in row.items()}
    for i, existing in enumerate(rows):
        if existing.get("run_id") == incoming.get("run_id"):
            # Merge: keep existing value when incoming is empty
            merged = dict(existing)
            for k, v in incoming.items():
                if v != "":
                    merged[k] = v
            rows[i] = merged
            updated = True
            break
    if not updated:
        rows.append(incoming)

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    results_path.write_text(out.getvalue())


if __name__ == "__main__":
    sys.exit(main())
