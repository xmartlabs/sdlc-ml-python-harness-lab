"""The release job CI runs: decide the version, build the manifest, stage it.

Publishing is a separate, human-approved step (see ops/RUNBOOK.md). This job
only prepares `dist/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from release.artifact import manifest
from release.changelog import render, required_bump
from release.version import bump


def prepare(current: str, subjects: list[str], files: list[Path], dist: Path) -> str:
    version = bump(current, required_bump(subjects))
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "manifest.json").write_text(manifest(version, files))
    (dist / "CHANGELOG.md").write_text(render(version, subjects))
    return version


if __name__ == "__main__":  # pragma: no cover - CI entry point
    root = Path(__file__).resolve().parent.parent
    subjects = (root / "ops" / "pending_commits.txt").read_text().splitlines()
    current = (root / "VERSION").read_text().strip()
    payload = sorted((root / "payload").glob("*"))
    print(prepare(current, subjects, payload, root / "dist"))
    sys.exit(0)
