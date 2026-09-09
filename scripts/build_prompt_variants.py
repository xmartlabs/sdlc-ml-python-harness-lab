"""Regenerate every ``harness`` prompt variant from its ``default`` sibling.

A case exposes exactly two variants (ADR-0047): ``default``, which states the
task and says nothing about the harness, and ``harness``, which is the shared
preamble concatenated onto *the identical task body*. Generating the second
from the first is what keeps the pair a single-factor comparison — a hand-edited
``harness`` prompt would eventually drift into asking for a different task, and
the resulting delta would be unattributable.

Run as ``make prompts``. ``make prompts-check`` fails when a file is stale, so
CI stays read-only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PREAMBLE = REPO_ROOT / "benchmarks" / "_harness_preamble.md"


def pairs() -> list[tuple[Path, Path]]:
    """Every ``(default, harness)`` prompt pair under ``benchmarks/``."""
    found = []
    for default in sorted(REPO_ROOT.glob("benchmarks/*/prompt_default.md")):
        found.append((default, default.with_name("prompt_harness.md")))
    return found


def expected(default: Path) -> str:
    return PREAMBLE.read_text() + default.read_text()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when a harness prompt is stale instead of writing it",
    )
    args = parser.parse_args()

    found = pairs()
    if not found:
        print("no prompt_default.md found under benchmarks/", file=sys.stderr)
        return 1

    stale = []
    for default, harness in found:
        want = expected(default)
        have = harness.read_text() if harness.exists() else None
        if have == want:
            continue
        stale.append(harness)
        if not args.check:
            harness.write_text(want)

    rel = REPO_ROOT
    if args.check and stale:
        for path in stale:
            print(f"stale: {path.relative_to(rel)}", file=sys.stderr)
        print("run `make prompts` to regenerate", file=sys.stderr)
        return 1
    for path in stale:
        print(f"wrote {path.relative_to(rel)}")
    print(f"{len(found)} prompt pair(s) {'checked' if args.check else 'up to date'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
