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
        "slug": "demo",
        "file": "demo-guide.md",
        "group": "Start here",
        "title": "Demo guide",
        "description": "The lab in 20 minutes, and how to try it on your own project.",
    },
    {
        "slug": "small-models",
        "file": "guide-small-models.md",
        "group": "Start here",
        "title": "Small & self-hosted models",
        "description": "Which tier a model does reliably, and whether the harness raises it.",
    },
    {
        "slug": "onboarding",
        "file": "onboarding.md",
        "group": "Start here",
        "title": "Onboarding",
        "description": "Credentials (gateway or Claude), .env, first run, troubleshooting.",
    },
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
        "description": "claude in Docker, codex in host mode, pick a native model, score, compare.",
    },
    {
        "slug": "improvement-cycle",
        "file": "improvement-cycle.md",
        "group": "How-to",
        "title": "Continuous-improvement cycle",
        "description": "Turn a run's audit into GitHub issues for your repos.",
    },
    {
        "slug": "target-repositories",
        "file": "guide-target-repositories.md",
        "group": "How-to",
        "title": "Target repositories",
        "description": "Clone it yourself, pin a commit, gate check, derive cases.",
    },
    {
        "slug": "skill-validation",
        "file": "guide-skill-validation.md",
        "group": "How-to",
        "title": "Validate a skill",
        "description": "Import, A/B, enforce invocation, promote.",
    },
    {
        "slug": "run-continuation",
        "file": "guide-run-continuation.md",
        "group": "How-to",
        "title": "Continue a run",
        "description": "Push a stopped run further with a new prompt.",
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
    {
        "slug": "langfuse",
        "file": "guide-langfuse-observability.md",
        "group": "Reference",
        "title": "Langfuse observability",
        "description": "Read a run turn by turn.",
    },
    {
        "slug": "known-limitations",
        "file": "known-limitations.md",
        "group": "Reference",
        "title": "Known limitations",
        "description": "Accepted debt, stated up front.",
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
