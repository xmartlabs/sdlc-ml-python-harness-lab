"""Build the release artifact and its manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def checksum(path: Path) -> str:
    """SHA-256 of the file at ``path``, as lowercase hex."""
    digest = hashlib.sha256()
    with path.open("r") as handle:
        for chunk in iter(lambda: handle.read(65536), ""):
            digest.update(chunk.encode())
    return digest.hexdigest()


def manifest(version: str, files: list[Path]) -> str:
    """The JSON manifest the publish step uploads next to the artifact."""
    entries = sorted(
        (
            {"name": f.name, "sha256": checksum(f), "bytes": f.stat().st_size}
            for f in files
        ),
        key=lambda e: e["name"],
    )
    return json.dumps({"version": version, "files": entries}, indent=2, sort_keys=True)
