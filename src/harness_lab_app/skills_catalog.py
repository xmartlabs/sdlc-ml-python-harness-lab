"""Read-only view of the governed skill catalog for the web app.

Thin adapter over the skills_sync engine so the API layer never imports
``ml_python_base`` directly. Used to list skills in the run form and to
validate per-run skill selections (ADR: per-run skill ablation).
"""

from __future__ import annotations

from typing import Any

from harness_lab_app.config import get_settings
from ml_python_base.skills_sync.discovery import discover_skills


def list_harness_skills() -> list[dict[str, Any]]:
    """Governed skills available to a run: name, kind, description."""
    skills = discover_skills(get_settings().repo_root)
    return [
        {"name": skill.name, "kind": skill.kind, "description": skill.description}
        for skill in skills
    ]


def known_skill_names() -> set[str]:
    return {skill.name for skill in discover_skills(get_settings().repo_root)}


def skill_footprints() -> dict[str, dict[str, Any]]:
    """What each governed skill costs before anyone opens it, and when they do.

    ``description_chars`` is the always-paid part: every adapter file lists the
    description, so every run carries it in context. ``body_bytes`` is paid
    only when the skill is consulted (an external skill counts every file in
    its directory, since the agent may follow its references).
    """
    footprints: dict[str, dict[str, Any]] = {}
    for skill in discover_skills(get_settings().repo_root):
        source = skill.source_path
        files = [source] if source.is_file() else sorted(source.rglob("*"))
        body = sum(path.stat().st_size for path in files if path.is_file())
        footprints[skill.name] = {
            "kind": skill.kind,
            "description_chars": len(skill.description),
            "body_bytes": body,
        }
    return footprints
