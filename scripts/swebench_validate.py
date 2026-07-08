#!/usr/bin/env python3
"""Objective validation for a SWE-bench run (runs in the validation container).

Invoked by the case's validation_commands as:
    python /app/scripts/swebench_validate.py --run <run_id>

Reads data/runs/<run_id>/case_spec.json, (re)installs the archived repo at
data/runs/<run_id>/workspace/ into the container interpreter, runs the
FAIL_TO_PASS + PASS_TO_PASS subset, and prints a parseable verdict:

    SWEBENCH: resolved=<bool> F2P=<x>/<n> P2P=<y>/<m>

Exit code is 0 when resolved (all F2P pass and no P2P regresses), else 1 — so
the existing validation pipeline records objective pass/fail (ADR-0009).
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from swebench_common import evaluate, install_repo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Run id")
    args = parser.parse_args(argv)

    run_dir = _ROOT / "data" / "runs" / args.run
    spec_path = run_dir / "case_spec.json"
    repo_dir = run_dir / "workspace"
    if not spec_path.exists():
        print(f"SWEBENCH: resolved=False (no case_spec.json at {spec_path})")
        return 1
    if not repo_dir.exists():
        print(f"SWEBENCH: resolved=False (no archived workspace at {repo_dir})")
        return 1

    spec = json.loads(spec_path.read_text())
    f2p = spec.get("fail_to_pass", [])
    p2p = spec.get("pass_to_pass", [])
    python = sys.executable

    print(f"→ Installing archived repo {repo_dir} …")
    installed = install_repo(repo_dir, python, spec.get("install_pins"))
    if installed.returncode != 0:
        print(installed.stdout[-1000:])
        print(installed.stderr[-1000:])
        print("SWEBENCH: resolved=False (install failed)")
        return 1

    print(f"→ Running F2P ({len(f2p)}) + P2P ({len(p2p)}) …")
    result = evaluate(repo_dir, python, f2p, p2p)
    print(
        f"SWEBENCH: resolved={result['resolved']} "
        f"F2P={result['f2p_passed']}/{result['f2p_total']} "
        f"P2P={result['p2p_passed']}/{result['p2p_total']}"
    )
    print(result["raw"][-2000:])
    return 0 if result["resolved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
