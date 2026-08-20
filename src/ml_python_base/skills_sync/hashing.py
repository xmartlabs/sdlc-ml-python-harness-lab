"""Hashing helpers that reproduce the legacy shell pipeline bit-for-bit.

The Antigravity manifest and the ``sync`` dedup logic depend on these digests
matching the previous Makefile bash exactly. The folder-hash mirrors:

    cd <folder>
    find . -type f -print | LC_ALL=C sort \
        | while IFS= read -r f; do printf '%s\n' "$f"; shasum -a 256 "$f"; done \
        | shasum -a 256 | awk '{print $1}'

``shasum -a 256 <file>`` prints ``<hexdigest>  <path>`` (two spaces, text mode),
and that line is part of the hashed stream — so the path appears twice per file.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_CHUNK = 65536


def file_sha256(path: Path) -> str:
    """Return the hex SHA-256 of a file's contents."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_files(folder: Path) -> list[tuple[str, Path]]:
    """Collect ``find . -type f`` equivalents as (relative_posix, absolute)."""
    collected: list[tuple[str, Path]] = []
    for dirpath, _dirnames, filenames in os.walk(folder):  # followlinks=False
        for filename in filenames:
            absolute = Path(dirpath) / filename
            if absolute.is_symlink():  # find -type f excludes symlinks
                continue
            relative = os.path.relpath(absolute, folder).replace(os.sep, "/")
            collected.append((f"./{relative}", absolute))
    return collected


def folder_hash(folder: Path) -> str:
    """Return the deterministic folder digest matching the legacy pipeline."""
    files = _regular_files(folder)
    # LC_ALL=C sort == bytewise ordering of the printed path lines.
    files.sort(key=lambda item: item[0].encode("utf-8"))

    stream = bytearray()
    for relative, absolute in files:
        stream += f"{relative}\n".encode()
        stream += f"{file_sha256(absolute)}  {relative}\n".encode()
    return hashlib.sha256(bytes(stream)).hexdigest()
