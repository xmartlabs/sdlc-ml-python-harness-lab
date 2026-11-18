#!/usr/bin/env python3
"""Which skill profile should each model carry? A profile x model grid (ADR-0085).

One `harness_by_model` experiment (ADR-0066): every profile in
`.github/skill-profiles.toml` (plus `full`) crossed with every model. A profile
reaches the run as the `skills_disabled` list that turns the full catalog into
it, so an arm is an ordinary, reproducible ablation — no new run field.

What it answers: per model, does a smaller profile lose success, take longer to
resolve or cost more than `full`? A small model may do *better* with fewer
skills (less to read past) and a large one may not need them at all — the
grid reads that interaction instead of assuming its direction.

Usage:
    uv run python scripts/profile_experiment.py \\
        --models claude-haiku-4-5-20251001 claude-opus-5-5 \\
        --cases tetris-v2 cli-textstats-v1 clinic-slots-t1 \\
        [--profiles full minimal] [--reps 3] [--harness claude] [--launch]

Cost: profiles x models x cases x reps runs. Three profiles, two models, three
cases, three repetitions is 54 runs.
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
from ml_python_base.skills_sync.profiles import (  # noqa: E402
    FULL,
    disabled_for,
    load_profiles,
)

HARNESS_PROMPT = "harness"


def _slug(text: str) -> str:
    return text.replace("/", "-").replace(".", "-").replace("_", "-").lower()


def design(
    *,
    cases: list[str],
    models: list[str],
    profiles: dict[str, list[str]],
    reps: int,
) -> ExperimentCreate:
    """The grid. ``profiles`` maps each profile name to its ``skills_disabled``.

    The control cell is the first profile on the first model (``full`` by
    default); `harness_by_model` reads effects per model against the first
    harness level, so the role only satisfies the schema.
    """
    conditions: list[ExperimentConditionCreate] = []
    for model in models:
        for index, (name, disabled) in enumerate(profiles.items()):
            first = index == 0 and model == models[0]
            conditions.append(
                ExperimentConditionCreate(
                    label=f"{name}-{_slug(model)}"[:64],
                    role=ConditionRole.control if first else ConditionRole.treatment,
                    overrides={
                        "prompt_variant": HARNESS_PROMPT,
                        "executor_model": model,
                        "skills_disabled": list(disabled),
                    },
                )
            )
    names = "-".join(profiles)
    return ExperimentCreate(
        experiment_id=f"profiles-{names}-{len(models)}m"[:64],
        name=f"Skill profiles x model ({', '.join(profiles)})",
        question=(
            "Per model, which skill profile resolves as many cases, as early and "
            "as cheaply as the full catalog?"
        ),
        mode=ExperimentMode.harness_by_model,
        case_ids=list(cases),
        prompt_variants=[],
        repetitions=reps,
        family_id=f"profiles-{names}"[:48],
        notes=(
            "Skill profiles (ADR-0085), each run as the skills_disabled list "
            "that turns the full catalog into it. Read per model; pick the "
            "smallest profile that is non-inferior to full for that model."
        ),
        conditions=conditions,
    )


def resolve_profiles(names: list[str], catalog: list[str]) -> dict[str, list[str]]:
    defined = load_profiles(REPO_ROOT)
    unknown = sorted(set(names) - set(defined))
    if unknown:
        sys.exit(
            f"❌ unknown profile(s): {', '.join(unknown)}. "
            f"Defined: {', '.join(sorted(defined))}"
        )
    return {name: disabled_for(catalog, defined, name) for name in names}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--cases", nargs="+", required=True)
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=[FULL, "minimal"],
        help="first one is the reference level (default: full minimal)",
    )
    parser.add_argument(
        "--harness", default="claude", choices=["claude", "opencode", "codex"]
    )
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--launch", action="store_true", help="queue the runs")
    args = parser.parse_args(argv)

    if len(args.models) < 2:
        sys.exit("❌ a profile x model grid needs at least two models")
    if len(args.profiles) < 2:
        sys.exit("❌ compare at least two profiles")
    catalog = sorted(known_skill_names())
    profiles = resolve_profiles(args.profiles, catalog)
    data = design(
        cases=args.cases, models=args.models, profiles=profiles, reps=args.reps
    )
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
        runs = len(data.conditions) * len(args.cases) * args.reps
        print(f"✓ {experiment.experiment_id}  ({runs} runs)")
        for name, disabled in profiles.items():
            print(
                f"  {name:<10} carries {len(catalog) - len(disabled)} of {len(catalog)}"
            )
        for warning in warnings:
            print(f"    ⚠ {warning}")
        if args.launch:
            base = RunConfigCreate(
                case_id=data.case_ids[0],
                prompt_variant=HARNESS_PROMPT,
                harness=args.harness,
                executor_model=args.models[0],
                score_mode="manual",
                max_steps=args.max_steps,
            )
            launched = service.launch(experiment.experiment_id, base)
            print(f"  launched {len(launched)} run(s)")
        print(f"\nread it at: /api/experiments/{experiment.experiment_id}/summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
