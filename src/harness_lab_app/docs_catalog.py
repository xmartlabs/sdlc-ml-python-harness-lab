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
        "slug": "start",
        "file": "README.md",
        "group": "Start here",
        "title": "What the lab is for",
        "description": "What a developer takes away, and the six journeys.",
    },
    {
        "slug": "create-case",
        "file": "journey-create-a-case.md",
        "group": "Start here",
        "title": "1. Create a case",
        "description": "From an idea with AI, from SWE-bench, from your repo, or by hand.",
    },
    {
        "slug": "run",
        "file": "journey-run-and-compare.md",
        "group": "Start here",
        "title": "2. Run and compare",
        "description": "Models, harnesses and skills; repeat, continue, compare.",
    },
    {
        "slug": "read-results",
        "file": "journey-read-results.md",
        "group": "Start here",
        "title": "3. Read the results",
        "description": "What to look at first, and when to believe it.",
    },
    {
        "slug": "bring-your-own",
        "file": "journey-bring-your-repo-or-skill.md",
        "group": "Start here",
        "title": "4. Bring your repo or skill",
        "description": "Audit a repository's harness; measure a skill.",
    },
    {
        "slug": "take-it-home",
        "file": "journey-take-it-home.md",
        "group": "Start here",
        "title": "5. Take it home",
        "description": "Copy the harness, or one skill, into your project.",
    },
    {
        "slug": "improve-template",
        "file": "journey-improve-the-template.md",
        "group": "Start here",
        "title": "6. Improve the template",
        "description": "From evidence to a better ml-python-base.",
    },
    {
        "slug": "onboarding",
        "file": "onboarding.md",
        "group": "Setup",
        "title": "Onboarding",
        "description": "Credentials (gateway or Claude), .env, first run, troubleshooting.",
    },
    {
        "slug": "demo",
        "file": "demo-guide.md",
        "group": "Setup",
        "title": "Demo guide",
        "description": "The lab in 20 minutes, for showing it to a team.",
    },
    {
        "slug": "web-ui",
        "file": "guide-web-ui.md",
        "group": "Reference",
        "title": "Web UI guide",
        "description": "Every screen in detail.",
    },
    {
        "slug": "case-formation",
        "file": "guide-case-formation.md",
        "group": "Reference",
        "title": "Case formation",
        "description": "What makes a case measure something, and what is refused.",
    },
    {
        "slug": "3d-game-cases",
        "file": "guide-3d-game-cases.md",
        "group": "Reference",
        "title": "3D game cases",
        "description": "Cases with their own engine image, graded by driving the game, playable in the browser.",
    },
    {
        "slug": "local-models",
        "file": "guide-local-models.md",
        "group": "Reference",
        "title": "Tune your local model",
        "description": "Fit, load and test an LM Studio or Ollama configuration on your machine.",
    },
    {
        "slug": "small-models",
        "file": "guide-small-models.md",
        "group": "Reference",
        "title": "Small & self-hosted models",
        "description": "Which tier a model does reliably, and whether the harness raises it.",
    },
    {
        "slug": "harness-adapters",
        "file": "harness-adapters.md",
        "group": "Reference",
        "title": "Harness adapters",
        "description": "How claude / opencode / codex are wired; host mode.",
    },
    {
        "slug": "claude-codex",
        "file": "runbook-claude-codex.md",
        "group": "Reference",
        "title": "claude & codex runbook",
        "description": "claude in Docker, codex in host mode.",
    },
    {
        "slug": "target-repositories",
        "file": "guide-target-repositories.md",
        "group": "Reference",
        "title": "Target repositories",
        "description": "Clone, pin a commit, gate check, derive cases.",
    },
    {
        "slug": "skill-validation",
        "file": "guide-skill-validation.md",
        "group": "Reference",
        "title": "Skill validation",
        "description": "Import, A/B, enforce invocation, promote.",
    },
    {
        "slug": "run-continuation",
        "file": "guide-run-continuation.md",
        "group": "Reference",
        "title": "Run continuation",
        "description": "Push a stopped run further with a new prompt.",
    },
    {
        "slug": "swebench",
        "file": "swebench-curated-set.md",
        "group": "Reference",
        "title": "SWE-bench cases that run",
        "description": "Validated instances and how to add more.",
    },
    {
        "slug": "improvement-cycle",
        "file": "improvement-cycle.md",
        "group": "Reference",
        "title": "Improvement cycle",
        "description": "Audit → proposals → issues, in detail.",
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
    """Index of docs, display order preserved.

    ``file`` lets the Guide open a link between two catalogued docs in place —
    the journeys link each other by file name.
    """
    return [
        {k: entry[k] for k in ("slug", "title", "description", "group", "file")}
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
