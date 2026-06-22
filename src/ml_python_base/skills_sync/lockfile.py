"""Generate ``skills-lock.json`` byte-compatibly with the legacy bash.

To keep the drift check (``make check-sync``) stable across clock movement,
timestamps are only refreshed when the external skill set or its hashes change.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ml_python_base.skills_sync.discovery import EXTERNAL_DIR, SKILL_FILE
from ml_python_base.skills_sync.hashing import file_sha256

LOCK_FILE = Path("skills-lock.json")


def _external_entries(
    root: Path, external_dir: Path
) -> list[tuple[str, str, str, str]]:
    """Return ``(name, path, skill_file, hash)`` per external skill, sorted."""
    base = root / external_dir
    entries: list[tuple[str, str, str, str]] = []
    if not base.is_dir():
        return entries
    for directory in sorted(base.iterdir(), key=lambda p: p.name):
        if not directory.is_dir():
            continue
        skill_file = directory / SKILL_FILE
        if not skill_file.is_file():
            continue
        rel_dir = (external_dir / directory.name).as_posix()
        rel_file = (external_dir / directory.name / SKILL_FILE).as_posix()
        entries.append((directory.name, rel_dir, rel_file, file_sha256(skill_file)))
    return entries


def _read_existing(lock_path: Path) -> dict:
    if not lock_path.is_file():
        return {}
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def render_lock(
    root: Path,
    now_iso: str,
    external_dir: Path = EXTERNAL_DIR,
    lock_path: Path | None = None,
) -> str:
    """Build the lock content, preserving timestamps when nothing changed."""
    lock_path = lock_path or (root / LOCK_FILE)
    entries = _external_entries(root, external_dir)
    existing = _read_existing(lock_path).get("skills", {})
    existing_meta = _read_existing(lock_path)

    unchanged = _is_unchanged(entries, existing)
    generated_at = existing_meta.get("generatedAt", now_iso) if unchanged else now_iso

    header = (
        f'{{\n  "version": 1,\n  "generatedAt": "{generated_at}",\n  "skills": {{\n'
    )
    blocks: list[str] = []
    for name, rel_dir, rel_file, digest in entries:
        synced_at = _synced_at(name, rel_dir, rel_file, digest, existing, now_iso)
        blocks.append(
            f'    "{name}": {{\n'
            '      "source": "synced-local",\n'
            '      "sourceType": "directory",\n'
            f'      "path": "{rel_dir}",\n'
            f'      "skillFile": "{rel_file}",\n'
            f'      "computedHash": "{digest}",\n'
            f'      "syncedAt": "{synced_at}"\n'
            "    }"
        )
    return header + ",\n".join(blocks) + "\n  }\n}\n"


def write_lock(
    root: Path,
    now_iso: str,
    external_dir: Path = EXTERNAL_DIR,
    lock_path: Path | None = None,
) -> None:
    lock_path = lock_path or (root / LOCK_FILE)
    content = render_lock(root, now_iso, external_dir, lock_path)
    _atomic_write(lock_path, content)


def _is_unchanged(entries: list[tuple[str, str, str, str]], existing: dict) -> bool:
    if len(entries) != len(existing):
        return False
    for name, rel_dir, rel_file, digest in entries:
        prior = existing.get(name)
        if prior is None:
            return False
        if (
            prior.get("computedHash") != digest
            or prior.get("path") != rel_dir
            or prior.get("skillFile") != rel_file
        ):
            return False
    return True


def _synced_at(
    name: str,
    rel_dir: str,
    rel_file: str,
    digest: str,
    existing: dict,
    now_iso: str,
) -> str:
    prior = existing.get(name)
    if (
        prior
        and prior.get("computedHash") == digest
        and prior.get("path") == rel_dir
        and prior.get("skillFile") == rel_file
    ):
        return prior.get("syncedAt", now_iso)
    return now_iso


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-lock-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
