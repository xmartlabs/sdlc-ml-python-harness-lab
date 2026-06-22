"""Declarative, registry-driven engine that projects governed skills and adapter
instructions into each AI tool's native layout.

The single source of truth lives under ``.github/`` (skills + governance) and a
declarative registry (``adapters/registry.toml``). This package replaces the
hand-written, per-tool bash that previously lived in the Makefile.
"""

from ml_python_base.skills_sync.models import (
    Governance,
    Registry,
    Skill,
    ToolSpec,
)

__all__ = ["Governance", "Registry", "Skill", "ToolSpec"]
