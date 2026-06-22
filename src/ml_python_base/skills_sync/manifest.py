"""Read and write the Antigravity ``.generated-manifest.tsv`` hash manifest."""

from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def read_manifest(path: Path) -> dict[str, str]:
    """Return ``{skill_name: folder_hash}`` from a manifest, or empty if absent."""
    if not path.is_file():
        return {}
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        name, _, digest = line.partition("\t")
        if name:
            entries[name] = digest
    return entries


def write_manifest(path: Path, hashes: dict[str, str]) -> None:
    """Write the manifest atomically.

    Insertion order (internal skills first, then external — both alphabetical)
    is preserved to stay byte-identical with the legacy generator. The caller
    builds ``hashes`` in discovery order, which is deterministic.
    """
    _atomic_write(path, render_manifest(hashes))


def render_manifest(hashes: dict[str, str]) -> str:
    """Return the manifest text without touching disk (used by ``check``)."""
    return "".join(f"{name}\t{digest}\n" for name, digest in hashes.items())


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-manifest-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
