#!/usr/bin/env python3
"""Create the harness ladder as one experiment family (ADR-0051).

The question "does the harness help, and by how much" is not one comparison but
four, each moving a single factor on top of the last:

    rung 1  bare              → governance-only     (Governance body)
    rung 2  governance-only   → +skills available   (Remove skills, inverted)
    rung 3  +skills available → +harness prompt     (Harness prompt)
    rung 4  +harness prompt   → +activation block   (Activation)

Every rung is an ordinary `harness_evaluation` experiment the wizard could have
designed by hand; this script only guarantees they are designed *consistently*
(same cases, same model, same repetitions) and tagged with one `family_id` so
the results page can read them as a ladder. Harness value is a function of the
(harness CLI, model) pair, so a family is always one pair: never compare rungs
across pairs.

Usage:
    uv run python scripts/ladder.py --cases tetris-v2 cli-textstats-v1 \\
        --harness claude --model claude-haiku-4-5-20251001 --reps 5 [--launch]

Without --launch the experiments are created as drafts (visible in the UI, not
running). With it, every rung's runs are queued — rungs run in parallel unless
the worker is single-slot, so budget for all of them at once.
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
from harness_lab_app.models import ConditionRole, ExperimentMode  # noqa: E402
from harness_lab_app.schemas import (  # noqa: E402
    ExperimentConditionCreate,
    ExperimentCreate,
    RunConfigCreate,
)
from harness_lab_app.skills_catalog import known_skill_names  # noqa: E402

# Prompt variants the two-variant shape guarantees on every case (ADR-0047).
PLAIN = "default"
HARNESS = "harness"


def rungs(catalog: list[str]) -> list[dict]:
    """The four rungs, each a control/treatment pair that moves one factor.

    `skills_disabled` on the bare arm is a declaration for the condition hash
    (the runner masks the catalog anyway, ADR-0049); on the governed arms it is
    the lever.
    """
    hidden = sorted(catalog)
    return [
        {
            "slug": "governance",
            "question": "Does the governance body alone (CLAUDE.md, hooks, subagents, .github/) help, with every skill hidden?",
            "control": (
                "bare",
                {
                    "harness_source": "bare",
                    "prompt_variant": PLAIN,
                    "skills_disabled": hidden,
                },
            ),
            "treatment": (
                "governance-only",
                {"prompt_variant": PLAIN, "skills_disabled": hidden},
            ),
        },
        {
            "slug": "skills",
            "question": "Does making the skill catalog available, on top of the governance body, help — without asking for it?",
            "control": (
                "governance-only",
                {"prompt_variant": PLAIN, "skills_disabled": hidden},
            ),
            "treatment": (
                "skills-available",
                {"prompt_variant": PLAIN, "skills_disabled": []},
            ),
        },
        {
            "slug": "prompt",
            "question": "Does the harness prompt (the preamble that directs the agent through the governed loop) help, on top of governance and skills?",
            "control": (
                "skills-available",
                {"prompt_variant": PLAIN, "skills_disabled": []},
            ),
            "treatment": (
                "harness-prompt",
                {"prompt_variant": HARNESS, "skills_disabled": []},
            ),
        },
        {
            "slug": "activation",
            "question": "Does asking the agent outright to invoke the skills (the activation block) help, on top of everything else?",
            "control": (
                "harness-prompt",
                {"prompt_variant": HARNESS, "skills_disabled": []},
            ),
            "treatment": (
                "activated",
                {
                    "prompt_variant": HARNESS,
                    "skills_disabled": [],
                    "skills_invoke": hidden,
                },
            ),
        },
    ]


def build(args: argparse.Namespace) -> list[ExperimentCreate]:
    catalog = sorted(known_skill_names())
    if not catalog:
        sys.exit("❌ no governed skills discovered — is .github/skills present?")
    model_slug = (
        args.model.replace("/", "-").replace(".", "-").replace("_", "-").lower()
    )
    family = args.family or f"ladder-{args.harness}-{model_slug}"[:48]
    created: list[ExperimentCreate] = []
    for index, rung in enumerate(rungs(catalog), start=1):
        control_label, control = rung["control"]
        treatment_label, treatment = rung["treatment"]
        created.append(
            ExperimentCreate(
                experiment_id=f"{family}-{index}-{rung['slug']}"[:64],
                name=f"Ladder rung {index}: {control_label} → {treatment_label}",
                question=rung["question"],
                mode=ExperimentMode.harness_evaluation,
                case_ids=list(args.cases),
                # Empty on purpose: `launch` expands every listed variant by every
                # arm and then applies the arm's override, so listing both here
                # would file each rung twice. Each arm names its own variant.
                prompt_variants=[],
                repetitions=args.reps,
                family_id=family,
                notes=(
                    f"Harness ladder rung {index} of 4 for ({args.harness}, "
                    f"{args.model}). Read with its siblings in family '{family}'."
                ),
                conditions=[
                    ExperimentConditionCreate(
                        label=control_label,
                        role=ConditionRole.control,
                        overrides=control,
                    ),
                    ExperimentConditionCreate(
                        label=treatment_label,
                        role=ConditionRole.treatment,
                        overrides=treatment,
                    ),
                ],
            )
        )
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        default=[],
        help="case ids (default: every active case)",
    )
    parser.add_argument(
        "--harness", default="claude", choices=["claude", "opencode", "codex"]
    )
    parser.add_argument(
        "--model", required=True, help="executor model, held fixed across the family"
    )
    parser.add_argument(
        "--reps", type=int, default=2, help="1 exploratory, 2 judgement, 5 validation"
    )
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--family", default="", help="override the generated family_id")
    parser.add_argument(
        "--launch", action="store_true", help="queue the runs, not just the drafts"
    )
    args = parser.parse_args(argv)

    init_db()
    with Session(get_engine()) as session:
        if not args.cases:
            from harness_lab_app.case_service import CaseService

            args.cases = [case.case_id for case in CaseService(session).list_cases()]
            if not args.cases:
                sys.exit("❌ no active cases — seed them first (POST /api/cases/seed)")
        designs = build(args)
        service = ExperimentService(session)
        for design in designs:
            try:
                experiment, warnings = service.create_experiment(design)
            except ExperimentError as exc:
                print(f"❌ {design.experiment_id}: {exc}")
                return 2
            print(f"✓ {experiment.experiment_id}")
            for warning in warnings:
                print(f"    ⚠ {warning}")
            if args.launch:
                base = RunConfigCreate(
                    case_id=design.case_ids[0],
                    prompt_variant=PLAIN,
                    harness=args.harness,
                    executor_model=args.model,
                    score_mode="manual",
                    max_steps=args.max_steps,
                )
                runs = service.launch(experiment.experiment_id, base)
                print(f"    launched {len(runs)} run(s)")
    family = designs[0].family_id
    print(f"\nfamily: {family}")
    print(f"read it at: /api/experiments?family_id={family}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
