#!/usr/bin/env python3
"""Import a hand-authored benchmark case from its task.yaml into the lab.

Parses `benchmarks/<case>/task.yaml` and persists a Case via
CaseService.import_task_yaml (the same path used to seed tetris-v1). Idempotent:
an already-imported case_id is left as-is.

  uv run python scripts/import_case.py --task benchmarks/cli-textstats/task.yaml
"""
# ruff: noqa: E402

import argparse
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sqlmodel import Session, select

from harness_lab_app.case_service import CaseService
from harness_lab_app.db import get_engine, init_db
from harness_lab_app.models import Case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task",
        required=True,
        help="Path to a case task.yaml, e.g. benchmarks/cli-textstats/task.yaml",
    )
    args = parser.parse_args(argv)

    task_path = Path(args.task)
    if not task_path.exists():
        print(f"❌ task.yaml not found: {task_path}", file=sys.stderr)
        return 1
    case_id = yaml.safe_load(task_path.read_text())["task_id"]

    init_db()
    with Session(get_engine()) as session:
        existing = session.exec(select(Case).where(Case.case_id == case_id)).first()
        if existing is not None:
            print(f"= already imported: {case_id} (no change)")
            return 0
        case = CaseService(session).import_task_yaml(task_path)
    print(
        f"✓ imported case: {case.case_id}  (type={case.type}, variants={list(case.prompt_variants)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
