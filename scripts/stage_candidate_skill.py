#!/usr/bin/env python3
"""Stage a case's candidate skill so an experiment can trial it (ADR-0087).

A case built to trial a skill ships the candidate next to its grader, under
``benchmarks/<case>/candidate-skill/<name>/``, where ``agent_hidden`` keeps it
out of every agent container: a control arm that could read the candidate would
not be a control. The treatment arm receives it the way every new skill does —
as a **staged** skill (ADR-0011/0017), projected into the run's native view.

This script is that hand-off: it reads the folder and stores the whole tree
through the same service the Skills page uses. Nothing in ``.github/`` changes;
promotion stays a human action once the trial has earned it (ADR-0086).

Usage:
    uv run python scripts/stage_candidate_skill.py --skill review_pull_request [--replace]
    uv run python scripts/stage_candidate_skill.py --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from harness_lab_app.candidate_skills import (  # noqa: E402  # noqa: E402
    CANDIDATE_GLOB,
    SOURCE_KIND,
    CandidateSkillError,
    candidates as _candidates,
    read_tree,
)

__all__ = ["CANDIDATE_GLOB", "SOURCE_KIND", "candidates", "main", "read_tree"]


def candidates(root: Path = REPO_ROOT) -> dict[str, Path]:
    """Every candidate skill in the case corpus, by folder name."""
    try:
        return _candidates(root)
    except CandidateSkillError as exc:
        raise SystemExit(f"❌ {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--skill", help="candidate folder name")
    parser.add_argument(
        "--replace", action="store_true", help="overwrite an already staged copy"
    )
    parser.add_argument("--list", action="store_true", help="list the candidates")
    args = parser.parse_args(argv)

    found = candidates()
    if args.list or not args.skill:
        for name, folder in found.items():
            print(f"  {name:32s} {folder.relative_to(REPO_ROOT)}")
        return 0 if found else 1
    if args.skill not in found:
        sys.exit(
            f"❌ no candidate '{args.skill}' under {CANDIDATE_GLOB}. "
            f"Known: {', '.join(found) or 'none'}"
        )
    folder = found[args.skill]

    from sqlmodel import Session

    from harness_lab_app.candidate_skills import stage
    from harness_lab_app.db import get_engine, init_db
    from harness_lab_app.skill_import import SkillImportError
    from harness_lab_app.staged_skill_service import StagedSkillError

    init_db()
    with Session(get_engine()) as session:
        try:
            result = stage(session, REPO_ROOT, folder, args.skill, replace=args.replace)
        except (SkillImportError, CandidateSkillError) as exc:
            sys.exit(f"❌ {exc}")
        except StagedSkillError as exc:
            hint = (
                " Add REPLACE=1 to overwrite it."
                if "already exists" in str(exc)
                else ""
            )
            sys.exit(f"❌ {exc}{hint}")
    print(f"✓ staged '{result.name}' ({result.files} file(s)) from {result.source}")
    for warning in result.warnings:
        print(f"    ⚠ {warning}")
    print(
        "  Next: Evaluate → New evaluation → Add skills → tick it, Enforce skill use."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
