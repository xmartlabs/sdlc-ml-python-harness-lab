"""Atomic YAML persistence for lifecycle manifests.

Writes use a temporary sibling plus an atomic rename so a crash never leaves a
partially written manifest (plan §16). YAML is emitted with keys in declaration
order to keep reviewed diffs stable.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import yaml

from harness_lab_app.harness_lifecycle.models import (
    CandidateManifest,
    InstalledManifest,
    Proposal,
)

if TYPE_CHECKING:
    from pathlib import Path

INSTALLED_VERSION_FILE = ".harness-version.yaml"
LEGACY_VERSION_FILE = ".template-version"
CANDIDATE_FILE = ".harness-candidate.yaml"


def dump_yaml(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def proposal_to_yaml(proposal: Proposal) -> str:
    return dump_yaml(proposal.model_dump(mode="json"))


def proposal_from_yaml(text: str) -> Proposal:
    return Proposal.model_validate(yaml.safe_load(text))


# --------------------------------------------------------------------------- #
# Installed version manifest (.harness-version.yaml) + legacy mirror
# --------------------------------------------------------------------------- #


def installed_manifest_to_yaml(manifest: InstalledManifest) -> str:
    return dump_yaml(manifest.model_dump(mode="json"))


def write_installed_manifest(root: Path, manifest: InstalledManifest) -> Path:
    path = root / INSTALLED_VERSION_FILE
    atomic_write_text(path, installed_manifest_to_yaml(manifest))
    return path


def read_installed_manifest(root: Path) -> InstalledManifest | None:
    path = root / INSTALLED_VERSION_FILE
    if not path.is_file():
        return None
    return InstalledManifest.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


def parse_legacy_template_version(text: str) -> dict:
    """Parse the legacy ``.template-version`` mirror into a plain dict.

    The legacy file is hand-rolled YAML-ish (``ref``/``commit``/``date``/``tool``
    plus a ``paths:`` list). Returns ``{ref, commit, date, tool, paths}``.
    """
    result: dict[str, object] = {}
    paths: list[str] = []
    in_paths = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if in_paths and line.lstrip().startswith("- "):
            paths.append(line.lstrip()[2:].strip())
            continue
        in_paths = False
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "paths":
            in_paths = True
        elif key in {"ref", "commit", "date", "tool"}:
            result[key] = value
    result["paths"] = paths
    return result


def read_legacy_template_version(root: Path) -> dict | None:
    path = root / LEGACY_VERSION_FILE
    if not path.is_file():
        return None
    return parse_legacy_template_version(path.read_text(encoding="utf-8"))


def write_candidate_manifest(worktree: Path, manifest: CandidateManifest) -> Path:
    """Write the candidate record inside its worktree (``.harness-candidate.yaml``)."""
    path = worktree / CANDIDATE_FILE
    atomic_write_text(path, dump_yaml(manifest.model_dump(mode="json")))
    return path
