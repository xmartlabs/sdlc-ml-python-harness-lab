#!/usr/bin/env python3
"""Create a leave-one-out pruning experiment for the harness (ADR-0076).

One experiment, one (harness CLI, model) pair:

    control      full harness (every skill available, harness prompt)
    treatment i  the same harness with exactly one skill removed

Its summary carries a `pruning` block: per removed skill, **removable**,
**keep** or **inconclusive**, read from paired intervals on success,
`cost_to_resolve_usd` and `progress_auc` against a margin declared up front.
"No difference" is never enough to remove anything.

Which skills to try first: by default the ones the per-step reward credits
least across the corpus (`GET /api/scores/step-credit`) — never consulted, or
consulted only in steps that made no progress. Pass --items to choose.

Usage:
    uv run python scripts/prune.py --model claude-haiku-4-5-20251001 \\
        --cases tetris-v2 cli-textstats-v1 clinic-slots-t1 --top 4 --reps 3 [--launch]

Cost: (1 + items) arms x cases x reps runs. Four items on three cases at three
repetitions is 45 runs. Harness value depends on the model: run one family per
model tier and remove a skill only for the tiers where it is removable.
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
    PRUNING_FAMILY_PREFIX,
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

HARNESS_PROMPT = "harness"


def shortlist(session: Session, catalog: list[str], top: int) -> list[str]:
    """The ``top`` skills with the least evidence of use, from the corpus."""
    from harness_lab_app.step_reward_service import StepRewardService

    known = set(catalog)
    items = StepRewardService(session).corpus()["items"]
    ranked = [
        row["item"].removeprefix("skill:")
        for row in items
        if row["item"].startswith("skill:")
        and row["item"].removeprefix("skill:") in known
    ]
    return ranked[:top]


def design(
    *, cases: list[str], items: list[str], model: str, harness: str, reps: int
) -> ExperimentCreate:
    model_slug = model.replace("/", "-").replace(".", "-").replace("_", "-").lower()
    family = f"{PRUNING_FAMILY_PREFIX}{harness}-{model_slug}"[:48]
    control = {"prompt_variant": HARNESS_PROMPT, "skills_disabled": []}
    conditions = [
        ExperimentConditionCreate(
            label="full-harness", role=ConditionRole.control, overrides=control
        )
    ]
    for item in items:
        conditions.append(
            ExperimentConditionCreate(
                label=f"without-{item}"[:64],
                role=ConditionRole.treatment,
                overrides={**control, "skills_disabled": [item]},
            )
        )
    return ExperimentCreate(
        experiment_id=f"{family}-{len(items)}x"[:64],
        name=f"Pruning: leave one skill out ({harness}, {model})",
        question=(
            "Which of these skills can be removed from the harness without the "
            "agent resolving fewer cases, later, or at a higher cost?"
        ),
        mode=ExperimentMode.harness_evaluation,
        case_ids=list(cases),
        prompt_variants=[],
        repetitions=reps,
        family_id=family,
        notes=(
            "Leave-one-out pruning (ADR-0076). Each treatment removes exactly "
            "one skill; read the `pruning` block of the summary. Items: "
            + ", ".join(items)
        ),
        conditions=conditions,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--cases", nargs="+", default=[], help="case ids")
    parser.add_argument(
        "--harness", default="claude", choices=["claude", "opencode", "codex"]
    )
    parser.add_argument("--model", required=True, help="executor model, held fixed")
    parser.add_argument("--items", nargs="*", default=[], help="skills to remove")
    parser.add_argument(
        "--top", type=int, default=4, help="without --items: how many to shortlist"
    )
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--launch", action="store_true", help="queue the runs")
    args = parser.parse_args(argv)

    init_db()
    with Session(get_engine()) as session:
        catalog = sorted(known_skill_names())
        if not catalog:
            sys.exit("❌ no governed skills discovered — is .github/skills present?")
        unknown = sorted(set(args.items) - set(catalog))
        if unknown:
            sys.exit(f"❌ not in the governed catalog: {', '.join(unknown)}")
        items = list(args.items) or shortlist(session, catalog, args.top)
        if not items:
            sys.exit("❌ nothing to prune — pass --items")
        if len(args.cases) < 3:
            print(
                "⚠  fewer than 3 cases: every verdict will be inconclusive "
                "(a bootstrap cannot estimate between-case spread from fewer)."
            )
        data = design(
            cases=args.cases,
            items=items,
            model=args.model,
            harness=args.harness,
            reps=args.reps,
        )
        service = ExperimentService(session)
        try:
            experiment, warnings = service.create_experiment(data)
        except ExperimentError as exc:
            print(f"❌ {exc}")
            return 2
        runs = (1 + len(items)) * len(args.cases) * args.reps
        print(f"✓ {experiment.experiment_id}  ({runs} runs)")
        print(f"  removing, one at a time: {', '.join(items)}")
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
