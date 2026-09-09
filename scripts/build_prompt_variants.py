"""Regenerate every ``harness`` prompt variant from its ``default`` sibling.

A case exposes exactly two variants (ADR-0047): ``default``, which states the
task and says nothing about the harness, and ``harness``, which is the shared
preamble concatenated onto *the identical task body*. Generating the second
from the first is what keeps the pair a single-factor comparison — a hand-edited
``harness`` prompt would eventually drift into asking for a different task, and
the resulting delta would be unattributable.

Run as ``make prompts``. ``make prompts-check`` fails when a file is stale, so
CI stays read-only.

The two halves are joined by :data:`SEPARATOR` rather than by a trailing blank
line inside the preamble. That is not a style choice: this repository runs
``end-of-file-fixer`` as a pre-commit hook, which trims a file back to exactly
one closing newline, so a preamble ending in a blank line cannot survive a
commit. Encoding the join here keeps it where a hook cannot reach it, and keeps
the generator and its test reading from one definition.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PREAMBLE = REPO_ROOT / "benchmarks" / "_harness_preamble.md"

#: What sits between the preamble and the task body. The preamble ends with a
#: `---` rule and every task body opens with a `# Task:` heading, so they need a
#: blank line between them; see the module docstring for why it lives here and
#: not at the end of the preamble file.
SEPARATOR = "\n"


def pairs() -> list[tuple[Path, Path]]:
    """Every ``(default, harness)`` prompt pair under ``benchmarks/``."""
    found = []
    for default in sorted(REPO_ROOT.glob("benchmarks/*/prompt_default.md")):
        found.append((default, default.with_name("prompt_harness.md")))
    return found


def expected(default: Path) -> str:
    """What ``prompt_harness.md`` must contain for this ``prompt_default.md``.

    The single definition of the pair. The drift test asserts against this
    function rather than re-deriving the concatenation, so the generator and
    the gate that guards it cannot disagree.
    """
    return PREAMBLE.read_text() + SEPARATOR + default.read_text()


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
