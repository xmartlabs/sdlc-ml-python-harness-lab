#!/usr/bin/env python3
"""A/B a rewritten skill against the governed one it would replace (ADR-0085).

The last step of harness optimization: after pruning has decided which skills
stay, make the survivors cheaper and better. A candidate is authored (or
imported) as a **staged** skill under its own name — `verify_changes-v2` — and
this script designs one `harness_evaluation` experiment:

    control    the governed skill, as is
    treatment  the governed skill disabled, the candidate staged in its place

Both arms carry the same base profile, so the only difference is the swap. The
same lever the lab has had since ADR-0011 (disable one, add one), named as one
intervention. Two things are worth rewriting, and they are different bets:

* the **description** — paid by every run, and what decides whether the skill
  fires at all; a candidate with a sharper trigger shows up as a higher
  `skill_invoked` rate in the treatment;
* the **body** — paid only when opened; a candidate with a shorter body shows
  up in `cost_to_resolve_usd` at equal success.

The summary's pruning-style margins apply: the candidate wins only if it is
non-inferior on success and better on cost or progress. Promotion stays a human
action — copy the candidate over the governed file, review, commit.

Usage:
    uv run python scripts/refine_skill.py --skill verify_changes \\
        --candidate verify_changes-v2 --model claude-haiku-4-5-20251001 \\
        --cases tetris-v2 cli-textstats-v1 clinic-slots-t1 \\
        [--profile minimal] [--invoke] [--reps 3] [--launch]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sqlmodel import Session  # noqa: E402

from harness_lab_app.db import get_engine, init_db  # noqa: E402
from harness_lab_app.experiment_service import (  # noqa: E402
    ExperimentError,
    ExperimentService,
)
from harness_lab_app.schemas import RunConfigCreate  # noqa: E402
from harness_lab_app.skill_refine import HARNESS_PROMPT, design  # noqa: E402
from harness_lab_app.skills_catalog import known_skill_names  # noqa: E402
from ml_python_base.skills_sync.profiles import (  # noqa: E402
    FULL,
    disabled_for,
    load_profiles,
)

__all__ = ["HARNESS_PROMPT", "design", "main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--skill", required=True, help="governed skill to replace")
    parser.add_argument("--candidate", required=True, help="staged skill name")
    parser.add_argument("--model", required=True)
    parser.add_argument("--cases", nargs="+", required=True)
    parser.add_argument("--profile", default=FULL, help="base profile both arms carry")
    parser.add_argument(
        "--invoke", action="store_true", help="ask both arms to use their version"
    )
    parser.add_argument(
        "--harness", default="claude", choices=["claude", "opencode", "codex"]
    )
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--launch", action="store_true", help="queue the runs")
    args = parser.parse_args(argv)

    catalog = sorted(known_skill_names())
    if args.skill not in catalog:
        sys.exit(f"❌ '{args.skill}' is not a governed skill")
    profiles = load_profiles(REPO_ROOT)
    if args.profile not in profiles:
        sys.exit(f"❌ unknown profile '{args.profile}'")
    try:
        data = design(
            skill=args.skill,
            candidate=args.candidate,
            cases=args.cases,
            model=args.model,
            reps=args.reps,
            base_disabled=disabled_for(catalog, profiles, args.profile),
            invoke=args.invoke,
            profile=args.profile,
        )
    except ValueError as exc:
        sys.exit(f"❌ {exc}")

    init_db()
    with Session(get_engine()) as session:
        from harness_lab_app.staged_skill_service import StagedSkillService

        if args.candidate not in StagedSkillService(session).active_names():
            sys.exit(
                f"❌ '{args.candidate}' is not an active staged skill. Create it on "
                "the Skills page (or POST /api/skills) first."
            )
        # The queue publisher, as the API passes it. Without it `launch` writes
        # the runs and never enqueues them: they sit `pending` with no error.
        from harness_lab_app.celery_app import enqueue_run

        service = ExperimentService(session, enqueue=enqueue_run)
        try:
            experiment, warnings = service.create_experiment(data)
        except ExperimentError as exc:
            print(f"❌ {exc}")
            return 2
        runs = 2 * len(args.cases) * args.reps
        print(f"✓ {experiment.experiment_id}  ({runs} runs)")
        for warning in warnings:
            print(f"    ⚠ {warning}")
        if args.launch:
            base = RunConfigCreate(
                case_id=data.case_ids[0],
                prompt_variant=HARNESS_PROMPT,
                harness=args.harness,
                executor_model=args.model,
                score_mode="manual",
                max_steps=args.max_steps,
            )
            launched = service.launch(experiment.experiment_id, base)
            print(f"  launched {len(launched)} run(s)")
        print(f"\nread it at: /api/experiments/{experiment.experiment_id}/summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
