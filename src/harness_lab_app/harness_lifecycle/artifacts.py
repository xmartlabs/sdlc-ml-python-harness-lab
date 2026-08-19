"""Ground-truth template artifact listing for proposal drafting.

Generated ``[HEP]`` issues repeatedly named files that do not exist in the
template repo (``.github/AGENTS.md`` in #43, ``adapters/opencode/AGENTS.md`` in
#47) because the drafting prompts never saw the template's real file layout —
the model guessed paths from a prose description. This module supplies the real
listing and a fail-closed validator:

1. :func:`template_artifact_listing` reads the file tree from the immutable
   template release tag (``git ls-tree`` against the ``template`` remote that
   :mod:`releases` maintains), filtered to the governed surfaces a proposal may
   edit. When no release tag is available it falls back to walking the lab's
   own working tree, which mirrors the template's adapter layout.
2. :func:`validate_affected_artifacts` rejects any drafted
   ``affected_artifacts`` entry that is not in the listing, naming the nearest
   real path so the failure is actionable.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from harness_lab_app.harness_lifecycle.releases import latest_release_tag, run_git

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Directory prefixes and root-level files a governed proposal may name. Kept
# deliberately wider than the sync allowlist: adapter prose (OPENCODE.md,
# AGENTS.md, ...) and harness plugins are legitimate HEP targets even though
# they are not template_sync governance_paths.
_LISTING_PREFIXES = (
    ".github/",
    "adapters/",
    ".opencode/",
    ".claude/",
    ".codex/",
    ".agents/",
    "docs/",
    "memory/",
)
_LISTING_ROOT_FILES = (
    "CLAUDE.md",
    "OPENCODE.md",
    "AGENTS.md",
    "LOCAL_AGENT.md",
    "opencode.json",
    "CHANGELOG.md",
)
# Noise the model must never target: generated mirrors and third-party syncs.
_LISTING_EXCLUDE_PREFIXES = (
    ".github/skills-external/",
    ".claude/skills/",
    ".opencode/skills/",
    ".codex/skills/",
    ".agents/skills/",
)


class UnknownArtifactError(ValueError):
    """A drafted affected_artifact names a path that does not exist upstream."""


def _keep(path: str) -> bool:
    if path in _LISTING_ROOT_FILES:
        return True
    if any(path.startswith(prefix) for prefix in _LISTING_EXCLUDE_PREFIXES):
        return False
    return any(path.startswith(prefix) for prefix in _LISTING_PREFIXES)


def template_artifact_listing(
    root: Path, *, ref: str | None = None, runner: Callable = run_git
) -> list[str]:
    """The governed file listing of the template at ``ref`` (sorted, filtered).

    ``ref`` defaults to the latest fetched template release tag. Returns an
    empty list when neither a release tag nor the fallback tree yields paths —
    callers treat an empty listing as "ground truth unavailable" and skip
    validation rather than failing every draft.
    """
    ref = ref or latest_release_tag(root, runner)
    if ref:
        result = runner(["ls-tree", "-r", "--name-only", ref], root, check=False)
        if result.returncode == 0:
            paths = [
                line.strip() for line in result.stdout.splitlines() if line.strip()
            ]
            listing = sorted(p for p in paths if _keep(p))
            if listing:
                return listing
    return _fallback_listing(root)


def _fallback_listing(root: Path) -> list[str]:
    """Walk the lab's own tree (it mirrors the template's adapter layout)."""
    listing: set[str] = set()
    for name in _LISTING_ROOT_FILES:
        if (root / name).is_file():
            listing.add(name)
    for prefix in _LISTING_PREFIXES:
        base = root / prefix.rstrip("/")
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if _keep(rel):
                listing.add(rel)
    return sorted(listing)


def render_listing_block(listing: list[str], *, clip: int = 8000) -> str:
    """The prompt block naming every real path a proposal may edit."""
    if not listing:
        return ""
    body = "\n".join(f"- {path}" for path in listing)
    if len(body) > clip:
        body = body[:clip].rsplit("\n", 1)[0] + "\n- …(listing clipped)"
    return (
        "## Template file listing (ground truth)\n\n"
        "Every affected_artifacts entry MUST be copied verbatim from this "
        "listing — these are the only files that exist:\n\n" + body
    )


def validate_affected_artifacts(artifacts: list[str], listing: list[str]) -> None:
    """Fail closed on any artifact path that is not in the ground-truth listing.

    A no-op when ``listing`` is empty (ground truth unavailable). Raises
    :class:`UnknownArtifactError` naming the nearest real path per offender.
    """
    if not listing:
        return
    known = set(listing)
    unknown = [a for a in artifacts if a not in known]
    if not unknown:
        return
    details = []
    for artifact in unknown:
        close = difflib.get_close_matches(artifact, listing, n=1, cutoff=0.4)
        hint = f" (nearest real path: {close[0]})" if close else ""
        details.append(f"'{artifact}'{hint}")
    raise UnknownArtifactError(
        "Drafted affected_artifacts name files that do not exist in the "
        "template: " + "; ".join(details)
    )
