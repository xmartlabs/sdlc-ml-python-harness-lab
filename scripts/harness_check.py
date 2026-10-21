#!/usr/bin/env python3
"""Run the lab's deterministic harness checks on any local git repository.

    make harness-check REPO=../my-project

No stack, no model, no network: it reads the committed tree at HEAD (or REF)
through git and prints the same checks the repository audit embeds (ADR-0065).
Exit code 1 when any check fails, so it can gate a CI job too.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from harness_lab_app.harness_checks import (
    FAIL,
    NOT_APPLICABLE,
    PASS,
    WARN,
    run_checks,
    summarize_checks,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_ICON = {FAIL: "✗ FAIL", WARN: "! WARN", PASS: "✓ PASS", NOT_APPLICABLE: "· N/A "}


def tree(repo: Path, ref: str) -> tuple[list[str], Callable[[str], str]]:
    """Tracked paths at ``ref`` and a reader for their committed content."""
    listing = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref],
        capture_output=True,
        text=True,
        check=True,
    )
    paths = listing.stdout.split("\n")

    def read(path: str) -> str:
        shown = subprocess.run(
            ["git", "-C", str(repo), "show", f"{ref}:{path}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return shown.stdout if shown.returncode == 0 else ""

    return [path for path in paths if path], read


def render(repo: Path, ref: str, checks: list[dict]) -> str:
    summary = summarize_checks(checks)
    lines = [
        f"Harness checks — {repo.resolve().name} @ {ref}",
        f"{summary[FAIL]} fail · {summary[WARN]} warn · {summary[PASS]} pass · "
        f"{summary[NOT_APPLICABLE]} n/a",
        "",
    ]
    for row in summary["ordered"]:
        lines.append(f"{_ICON[row['status']]}  {row['title']}")
        lines.append(f"         {row['evidence']}")
        if row["fix"]:
            lines.append(f"   fix → {row['fix']}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="path to a local git repository")
    parser.add_argument(
        "--ref", default="HEAD", help="commit, tag or branch (default HEAD)"
    )
    args = parser.parse_args(argv)
    given = Path(args.repo).expanduser()
    top = subprocess.run(
        ["git", "-C", str(given), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if not given.is_dir() or top.returncode != 0:
        print(f"❌ {given} is not inside a git repository.", file=sys.stderr)
        return 2
    # Any folder inside a repository checks the whole repository it belongs to.
    repo = Path(top.stdout.strip())
    try:
        paths, read = tree(repo, args.ref)
    except subprocess.CalledProcessError as error:
        print(
            f"❌ cannot read {args.ref} in {repo}: {error.stderr.strip()}",
            file=sys.stderr,
        )
        return 2
    checks = run_checks(paths, read)
    print(render(repo, args.ref, checks))
    return 1 if any(row["status"] == FAIL for row in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
