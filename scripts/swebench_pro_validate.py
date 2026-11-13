#!/usr/bin/env python3
"""Objective validation for a SWE-bench Pro run (worker/host side — needs docker).

Invoked by the case's validation_commands as:
    python /app/scripts/swebench_pro_validate.py --run <run_id>

Reads data/runs/<run_id>/case_spec.json, mounts the archived workspace at
data/runs/<run_id>/workspace/ into the instance's prebuilt image, runs the
official run_script + parser, and prints the same parseable verdict as the
image-free lane — so the validation panel and auto-score reuse
``parse_objective`` unchanged:

    SWEBENCH: resolved=<bool> F2P=<x>/<n> P2P=<y>/<m>

Exit code is 0 when resolved (every F2P and P2P test PASSED), else 1.
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from swebench_pro_common import docker_available, evaluate_containerized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Run id")
    parser.add_argument(
        "--repo-dir",
        default=None,
        help=(
            "Grade this tree instead of the archived workspace — one step's "
            "rebuilt tree for the per-step reward (ADR-0076). Must live under "
            "the repo so the docker daemon can bind-mount it."
        ),
    )
    args = parser.parse_args(argv)

    run_dir = _ROOT / "data" / "runs" / args.run
    spec_path = run_dir / "case_spec.json"
    repo_dir = Path(args.repo_dir).resolve() if args.repo_dir else run_dir / "workspace"
    if not spec_path.exists():
        print(f"SWEBENCH: resolved=False (no case_spec.json at {spec_path})")
        return 1
    if not repo_dir.exists():
        print(f"SWEBENCH: resolved=False (no archived workspace at {repo_dir})")
        return 1
    if not docker_available():
        print("SWEBENCH: resolved=False (docker unavailable — Pro lane needs it)")
        return 1

    spec = json.loads(spec_path.read_text())
    f2p = spec.get("fail_to_pass", [])
    p2p = spec.get("pass_to_pass", [])

    print(
        f"→ Running containerized F2P ({len(f2p)}) + P2P ({len(p2p)}) "
        f"in {spec.get('dockerhub_image', '?')} …"
    )
    result = evaluate_containerized(repo_dir, spec)
    print(
        f"SWEBENCH: resolved={result['resolved']} "
        f"F2P={result['f2p_passed']}/{result['f2p_total']} "
        f"P2P={result['p2p_passed']}/{result['p2p_total']}"
    )
    if result.get("error"):
        print(f"  ({result['error']})")
        # The F2P=0/n above is a placeholder, not a measurement: the suite
        # never produced a result. Marked on its own line so a reader that
        # must not mistake it for "no test passes" (the per-step reward,
        # ADR-0076) can tell, while the verdict line keeps its format.
        print(f"SWEBENCH-ERROR: {result['error']}")
    print(result["raw"][-2000:])
    return 0 if result["resolved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
