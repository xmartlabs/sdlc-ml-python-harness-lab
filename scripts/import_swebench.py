#!/usr/bin/env python3
"""Import a SWE-bench Lite instance into the lab as a Case.

Fetches the instance from the HuggingFace datasets-server (public, stdlib only),
normalizes it, and persists a Case via CaseService.import_swebench_instance.

  uv run python scripts/import_swebench.py --instance pallets__flask-4992
"""
# ruff: noqa: E402

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from sqlmodel import Session
from swebench_common import env_spec, fetch_instance, repo_url_from

from harness_lab_app.case_service import CaseService
from harness_lab_app.db import get_engine, init_db


def normalize(instance_id: str) -> dict:
    inst = fetch_instance(instance_id)
    spec = env_spec(inst["repo"])
    return {
        "instance_id": inst["instance_id"],
        "repo": inst["repo"],
        "base_commit": inst["base_commit"],
        "problem_statement": inst["problem_statement"],
        "test_patch": inst["test_patch"],
        "fail_to_pass": json.loads(inst["FAIL_TO_PASS"]),
        "pass_to_pass": json.loads(inst["PASS_TO_PASS"]),
        "version": inst.get("version", ""),
        "repo_url": repo_url_from(inst["repo"]),
        "python": spec["python"],
        "install_pins": spec["pins"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True, help="e.g. pallets__flask-4992")
    args = parser.parse_args(argv)

    print(f"→ Fetching {args.instance} from SWE-bench Lite …")
    spec = normalize(args.instance)
    print(
        f"  repo={spec['repo']} base={spec['base_commit'][:10]} "
        f"F2P={len(spec['fail_to_pass'])} P2P={len(spec['pass_to_pass'])} "
        f"python={spec['python']} pins={spec['install_pins']}"
    )

    init_db()
    with Session(get_engine()) as session:
        case = CaseService(session).import_swebench_instance(spec)
    print(
        f"✅ Case '{case.case_id}' ready (type={case.type}). Create a run from the UI."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
