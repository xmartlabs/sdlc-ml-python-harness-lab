"""Typed errors raised by the skills sync engine.

The CLI maps each error to a stable exit code so automation (Makefile, CI) can
react deterministically.
"""

from __future__ import annotations


class SkillsSyncError(Exception):
    """Base class for every recoverable engine error."""


class RegistryError(SkillsSyncError):
    """The registry file is missing, malformed, or fails validation."""


class UnsafeTargetError(SkillsSyncError):
    """Refusing to overwrite a path that the engine does not own."""


class DriftError(SkillsSyncError):
    """Generated artifacts on disk are stale relative to the source of truth."""

    def __init__(self, stale_paths: list[str]) -> None:
        self.stale_paths = stale_paths
        joined = "\n".join(f"  - {path}" for path in stale_paths)
        super().__init__(
            "Generated artifacts are stale. Run `make sync-skills` to refresh:\n"
            f"{joined}"
        )
