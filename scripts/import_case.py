#!/usr/bin/env python3
"""Import a hand-authored benchmark case from its task.yaml into the lab.

Parses `benchmarks/<case>/task.yaml` and persists a Case via
CaseService.import_task_yaml (the same path the startup seed uses). Idempotent:
an already-imported case_id is left as-is, because ADR-0025 makes the DB the
source of truth and a silent overwrite would discard operator edits.

`--refresh` is the explicit opt-out: it re-reads the *generated* fields
(`prompts`, `validation_commands`) from the YAML and leaves everything an
operator owns — name, description, tags, status — alone. Use it after a prompt
change, e.g. the ADR-0047 two-variant migration, where the case row would
otherwise keep pointing at variant names that no longer exist.

  uv run python scripts/import_case.py --task benchmarks/cli-textstats/task.yaml
  uv run python scripts/import_case.py --task benchmarks/cli-textstats/task.yaml --refresh
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
from harness_lab_app.models import Case, utc_now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task",
        required=True,
        help="Path to a case task.yaml, e.g. benchmarks/cli-textstats/task.yaml",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-read prompts and validation_commands into an already-imported case",
    )
    args = parser.parse_args(argv)

    task_path = Path(args.task)
    if not task_path.exists():
        print(f"❌ task.yaml not found: {task_path}", file=sys.stderr)
        return 1
    raw = yaml.safe_load(task_path.read_text())
    case_id = raw["task_id"]

    init_db()
    with Session(get_engine()) as session:
        existing = session.exec(select(Case).where(Case.case_id == case_id)).first()
        if existing is not None:
            if not args.refresh:
                print(f"= already imported: {case_id} (no change; --refresh to update)")
                return 0
            existing.prompt_variants = dict(raw.get("prompts") or {})
            if raw.get("validation_commands"):
                existing.validation_commands = [
                    str(command) for command in raw["validation_commands"]
                ]
            existing.updated_at = utc_now()
            session.add(existing)
            session.commit()
            session.refresh(existing)
            print(
                f"✓ refreshed case: {case_id}  "
                f"(variants={list(existing.prompt_variants)})"
            )
            return 0
        case = CaseService(session).import_task_yaml(task_path)
    print(
        f"✓ imported case: {case.case_id}  (type={case.type}, variants={list(case.prompt_variants)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
