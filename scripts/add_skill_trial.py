#!/usr/bin/env python3
"""Trial one catalog skill on top of a profile: does it earn a place? (ADR-0089).

Pruning (ADR-0076, ADR-0085) asks what can leave the harness; this asks the
opposite question, the one a small default profile needs: what should join it.
One `harness_evaluation` experiment:

    control    the base profile (``minimal`` by default), as a project carries it
    treatment  the same profile plus one governed skill, asked for (Enforce)

Asking for the skill measures "does it help when used", not "does the agent
find it" (ADR-0041): a skill that is never opened cannot win or lose. Read the
summary's `skill_invoked` before any delta — near 1 in the treatment, or the
arm measured nothing.

The skill earns a place only if the treatment wins on the cases it targets
without losing on a general one (ADR-0086: never trade success). Joining
``minimal`` is a stronger claim than an opt-in ``include``: every project
carries it. Promotion stays a human action — edit `.github/skill-profiles.toml`.

Usage:
    uv run python scripts/add_skill_trial.py --skill sensible_testing \\
        --model claude-haiku-4-5-20251001 \\
        --cases pr-review-v1 frontend-bugfix-v1 clinic-slots-t1 \\
        [--profile minimal] [--reps 3] [--launch]
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
from harness_lab_app.skill_trial import (  # noqa: E402
    DEFAULT_PROFILE,
    HARNESS_PROMPT,
    design,
)
from harness_lab_app.skills_catalog import known_skill_names  # noqa: E402
from ml_python_base.skills_sync.profiles import (  # noqa: E402
    disabled_for,
    load_profiles,
)

__all__ = ["design", "main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--skill", required=True, help="governed skill to add")
    parser.add_argument("--model", required=True)
    parser.add_argument("--cases", nargs="+", required=True)
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="base profile")
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
    if len(args.cases) < 3:
        print(
            "⚠  fewer than 3 cases: include a general case beside the target ones, "
            "or a loss outside the skill's niche goes unseen."
        )
    try:
        data = design(
            skill=args.skill,
            cases=args.cases,
            model=args.model,
            reps=args.reps,
            base_disabled=disabled_for(catalog, profiles, args.profile),
            profile=args.profile,
        )
    except ValueError as exc:
        sys.exit(f"❌ {exc}")

    init_db()
    with Session(get_engine()) as session:
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
