#!/usr/bin/env python3
"""Import a SWE-bench Pro instance into the lab as a Case.

Fetches the instance from the HuggingFace datasets-server and its evaluation
assets (run_script.sh + parser.py) from the official OSS repo (public, stdlib
only), normalizes it, and persists a Case via
CaseService.import_swebench_pro_instance. Python instances only for now
(docs/design-swe-bench-pro-lane.md).

  uv run python scripts/import_swebench_pro.py \\
      --instance instance_ansible__ansible-<sha>-v<sha>
"""
# ruff: noqa: E402

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from sqlmodel import Session

from harness_lab_app.case_service import CaseService
from harness_lab_app.db import get_engine, init_db
from harness_lab_app.swebench_pro_client import normalize_instance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--instance", required=True, help="e.g. instance_ansible__ansible-<sha>-v<sha>"
    )
    args = parser.parse_args(argv)

    print(f"→ Fetching {args.instance} from SWE-bench Pro …")
    spec = normalize_instance(args.instance)
    print(
        f"  repo={spec['repo']} ({spec['repo_language']}) "
        f"base={spec['base_commit'][:10]} "
        f"F2P={len(spec['fail_to_pass'])} P2P={len(spec['pass_to_pass'])}\n"
        f"  image={spec['dockerhub_image']}"
    )

    init_db()
    with Session(get_engine()) as session:
        case = CaseService(session).import_swebench_pro_instance(spec)
    print(
        f"✅ Case '{case.case_id}' ready (type={case.type}). Create a run from the UI."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
