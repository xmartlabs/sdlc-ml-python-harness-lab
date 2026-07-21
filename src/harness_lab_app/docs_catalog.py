"""Curated operator-facing docs surfaced in the web Guide page.

The frontend renders the *real* ``docs/*.md`` files in-app (no duplicated
content), so "what to do and how" is always available from the web. Only the
whitelisted slugs below are served — the file is resolved from this fixed map,
so there is no path traversal from the ``{slug}`` route parameter.
"""

from __future__ import annotations

from harness_lab_app.config import get_settings

# Order defines display order; `group` buckets them in the Guide page.
_DOCS: list[dict[str, str]] = [
    {
        "slug": "web-ui",
        "file": "guide-web-ui.md",
        "group": "Start here",
        "title": "Web UI guide",
        "description": "Overview, every screen, and the core loop.",
    },
    {
        "slug": "claude-codex",
        "file": "runbook-claude-codex.md",
        "group": "How-to",
        "title": "Test with claude & codex",
        "description": "Host mode, pick a native model, score, compare.",
    },
    {
        "slug": "improvement-cycle",
        "file": "improvement-cycle.md",
        "group": "How-to",
        "title": "Continuous-improvement cycle",
        "description": "Turn a run's audit into GitHub issues for your repos.",
    },
    {
        "slug": "swebench",
        "file": "swebench-curated-set.md",
        "group": "How-to",
        "title": "SWE-bench cases that run",
        "description": "Validated instances and how to add more.",
    },
    {
        "slug": "harness-adapters",
        "file": "harness-adapters.md",
        "group": "Reference",
        "title": "Harness adapters",
        "description": "How opencode / codex / claude are wired; host mode.",
    },
]


def list_docs() -> list[dict[str, str]]:
    """Index of docs (slug/title/description/group), display order preserved."""
    return [
        {k: entry[k] for k in ("slug", "title", "description", "group")}
        for entry in _DOCS
    ]


def read_doc(slug: str) -> dict[str, str] | None:
    """Return ``{slug, title, content}`` for a whitelisted doc, or None if unknown."""
    entry = next((d for d in _DOCS if d["slug"] == slug), None)
    if entry is None:
        return None
    path = get_settings().repo_root / "docs" / entry["file"]
    content = path.read_text() if path.exists() else "_Document not found on disk._"
    return {"slug": slug, "title": entry["title"], "content": content}
