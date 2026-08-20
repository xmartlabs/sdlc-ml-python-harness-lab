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
