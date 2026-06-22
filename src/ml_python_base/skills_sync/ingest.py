"""Ingest ad-hoc installed skills into the governed external folder.

Mirrors the legacy ``sync-skills`` ingestion: tools (notably Antigravity) may
drop skills into ``.agents/skills`` (or the legacy ``.agent/skills``). Skills
that are merely generated copies (hash matches the Antigravity manifest) are
skipped; genuinely new ad-hoc skills are copied into
``.github/skills-external/`` to become governed.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ml_python_base.skills_sync.discovery import EXTERNAL_DIR, SKILL_FILE
from ml_python_base.skills_sync.hashing import folder_hash
from ml_python_base.skills_sync.manifest import read_manifest

PRIMARY_SRC = Path(".agents/skills")
FALLBACK_SRC = Path(".agent/skills")
ANTIGRAVITY_MANIFEST = Path(".agents/skills/.generated-manifest.tsv")


@dataclass
class IngestResult:
    synced: int = 0
    skipped: int = 0
    generated_skipped: int = 0
    found_source: bool = False


def ingest_external_skills(root: Path) -> IngestResult:
    """Copy ad-hoc skills into the governed external folder; clean legacy dir."""
    dest = root / EXTERNAL_DIR
    dest.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(root / ANTIGRAVITY_MANIFEST)

    result = IngestResult()
    processed: set[str] = set()

    for src in (PRIMARY_SRC, FALLBACK_SRC):
        src_path = root / src
        if not src_path.is_dir():
            continue
        result.found_source = True
        for skill_dir in sorted(src_path.iterdir(), key=lambda p: p.name):
            if not skill_dir.is_dir():
                continue
            name = skill_dir.name
            if name in processed:
                continue
            processed.add(name)

            if not (skill_dir / SKILL_FILE).is_file():
                result.skipped += 1
                continue

            digest = folder_hash(skill_dir)
            # Only the primary source can host Antigravity-generated copies.
            if src == PRIMARY_SRC and manifest.get(name) == digest:
                result.generated_skipped += 1
                continue

            target = dest / name
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            _copy_tree(skill_dir, target)
            result.synced += 1

    _remove_legacy(root)
    return result


def _copy_tree(source_dir: Path, dest_dir: Path) -> None:
    for entry in sorted(source_dir.iterdir(), key=lambda p: p.name):
        target = dest_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copyfile(entry, target)


def _remove_legacy(root: Path) -> None:
    legacy = root / FALLBACK_SRC
    if legacy.exists():
        shutil.rmtree(legacy)
