#!/usr/bin/env python3
"""Print the skill census: which skills each model (or case, or harness) opens.

The first step of harness pruning (ADR-0085). For every group it lists each
governed skill with the share of measured runs that consulted it, the reward
earned by the steps that did, and what the skill costs to carry:

* ``desc`` — description characters, paid by **every** run (adapter listing);
* ``body`` — bytes of the skill itself, paid only when it is opened.

Co-occurrence, not cause: use it to choose what to ablate (``make prune``,
``GROUP=1`` to ablate the never-opened set at once), never to delete directly.

Usage:
    uv run python scripts/skill_census.py [--by model|case|harness] [--json]

While the stack runs, ``make skill-census`` executes this inside the api
container: the lab database must not be opened from the host across the
Docker Desktop bind mount.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sqlmodel import Session  # noqa: E402

from harness_lab_app.db import get_engine, init_db  # noqa: E402
from harness_lab_app.step_reward_service import (  # noqa: E402
    CENSUS_DIMENSIONS,
    StepRewardService,
)


def render(census: dict) -> str:
    """The census as plain text: one block per group, never-opened first."""
    footprints = census["footprints"]
    lines: list[str] = []
    if not census["groups"]:
        lines.append("No measured runs yet — the census needs per-step records.")
    for group, data in census["groups"].items():
        lines.append(f"\n{census['by']}: {group}  ({data['runs_measured']} runs)")
        lines.append(
            f"  {'skill':<34} {'runs':>5} {'share':>6} {'reward':>7} "
            f"{'desc':>5} {'body':>7}"
        )
        never: list[str] = []
        for row in data["items"]:
            if not row["item"].startswith("skill:"):
                continue
            name = row["item"].removeprefix("skill:")
            size = footprints.get(name, {})
            if row["runs"] == 0:
                never.append(name)
            lines.append(
                f"  {name:<34} {row['runs']:>5} {row['share_of_runs']:>6.0%} "
                f"{row['reward_when_consulted']:>7.2f} "
                f"{size.get('description_chars', 0):>5} {size.get('body_bytes', 0):>7}"
            )
        if never:
            paid = sum(footprints.get(n, {}).get("description_chars", 0) for n in never)
            lines.append(
                f"  → never opened: {len(never)} skill(s), {paid} description "
                f"chars carried by every run. Ablate as a group: "
                f'make prune GROUP=1 ITEMS="{" ".join(never)}" ...'
            )
    lines.append(f"\n{census['note']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--by", default="model", choices=sorted(CENSUS_DIMENSIONS))
    parser.add_argument("--json", action="store_true", help="raw JSON output")
    args = parser.parse_args(argv)

    init_db()
    with Session(get_engine()) as session:
        census = StepRewardService(session).census(by=args.by)
    print(json.dumps(census, indent=2) if args.json else render(census))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
