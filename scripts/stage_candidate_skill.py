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

CANDIDATE_GLOB = "benchmarks/*/candidate-skill/*/SKILL.md"
SOURCE_KIND = "manual"


def candidates(root: Path = REPO_ROOT) -> dict[str, Path]:
    """Every candidate skill in the case corpus, by folder name."""
    found: dict[str, Path] = {}
    for skill_md in sorted(root.glob(CANDIDATE_GLOB)):
        name = skill_md.parent.name
        if name in found:
            raise SystemExit(
                f"❌ candidate '{name}' is declared twice: {found[name]} and "
                f"{skill_md.parent}"
            )
        found[name] = skill_md.parent
    return found


def read_tree(folder: Path) -> dict[str, bytes]:
    """The candidate's files, relative to its folder; symlinks are skipped."""
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
    }


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

    from harness_lab_app.db import get_engine, init_db
    from harness_lab_app.skill_import import SkillImportError, build_skill
    from harness_lab_app.staged_skill_service import (
        StagedSkillError,
        StagedSkillService,
    )

    try:
        imported = build_skill(
            read_tree(folder),
            fallback_name=args.skill,
            source_url=str(folder.relative_to(REPO_ROOT)),
            source_kind=SOURCE_KIND,
        )
    except SkillImportError as exc:
        sys.exit(f"❌ {exc}")
    if imported.name != args.skill:
        sys.exit(
            f"❌ SKILL.md names '{imported.name}' but its folder is '{args.skill}'; "
            "make them match."
        )

    init_db()
    with Session(get_engine()) as session:
        try:
            skill = StagedSkillService(session).import_skill(
                imported, name=args.skill, replace=args.replace
            )
        except StagedSkillError as exc:
            hint = (
                " Add REPLACE=1 to overwrite it."
                if "already exists" in str(exc)
                else ""
            )
            sys.exit(f"❌ {exc}{hint}")
    print(
        f"✓ staged '{skill.name}' ({len(imported.files)} file(s)) from {folder.relative_to(REPO_ROOT)}"
    )
    for warning in imported.warnings:
        print(f"    ⚠ {warning}")
    print(
        "  Next: Evaluate → New evaluation → Add skills → tick it, Enforce skill use."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
