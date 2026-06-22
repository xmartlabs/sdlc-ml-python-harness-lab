"""Golden tests pinning the folder-hash to the legacy shell pipeline.

The Antigravity manifest and the ``sync`` dedup logic depend on this digest
matching the previous bash bit-for-bit. These are the highest-risk parity
guarantees in the migration.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ml_python_base.skills_sync.hashing import folder_hash
from ml_python_base.skills_sync.manifest import read_manifest

MANIFEST = Path(".agents/skills/.generated-manifest.tsv")

# macOS ships `shasum -a 256`; Ubuntu ships `sha256sum`. Both produce:
#   <hex>  <path>   (two spaces) — the same format our Python stream encodes.
# We build the pipeline at import time so skipif can use the same check.
_HASH_BIN: str = "sha256sum" if shutil.which("sha256sum") else "shasum -a 256"
_HASH_OUTER = "sha256sum" if shutil.which("sha256sum") else "shasum -a 256"

_SHELL_PIPELINE = (
    f'cd "$1" && find . -type f -print | LC_ALL=C sort '
    f'| while IFS= read -r f; do printf "%s\\n" "$f"; {_HASH_BIN} "$f"; done '
    f"| {_HASH_OUTER} | awk '{{print $1}}'"
)


def _skill_dirs(repo_root: Path) -> list[Path]:
    # Mirror the engine's discovery contract: a directory is a skill only when
    # it carries a SKILL.md. Runtimes (e.g. Antigravity) may leave behind empty
    # stub dirs that are not real skills and never reach the manifest.
    base = repo_root / ".agents/skills"
    return sorted(
        d for d in base.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()
    )


def test_folder_hash_matches_committed_manifest(repo_root: Path) -> None:
    manifest = read_manifest(repo_root / MANIFEST)
    assert manifest, "expected a populated Antigravity manifest"
    for skill_dir in _skill_dirs(repo_root):
        expected = manifest.get(skill_dir.name)
        assert expected is not None, f"missing manifest entry for {skill_dir.name}"
        assert folder_hash(skill_dir) == expected, skill_dir.name


@pytest.mark.skipif(
    shutil.which("shasum") is None and shutil.which("sha256sum") is None,
    reason="neither shasum nor sha256sum available",
)
def test_folder_hash_matches_shell_pipeline(repo_root: Path) -> None:
    for skill_dir in _skill_dirs(repo_root):
        result = subprocess.run(
            ["sh", "-c", _SHELL_PIPELINE, "sh", str(skill_dir)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert folder_hash(skill_dir) == result.stdout.strip(), skill_dir.name
