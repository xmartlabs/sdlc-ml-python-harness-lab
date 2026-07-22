"""Atomic YAML persistence for lifecycle manifests.

Writes use a temporary sibling plus an atomic rename so a crash never leaves a
partially written manifest (plan §16). YAML is emitted with keys in declaration
order to keep reviewed diffs stable.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import yaml

from harness_lab_app.harness_lifecycle.models import Proposal

if TYPE_CHECKING:
    from pathlib import Path


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
