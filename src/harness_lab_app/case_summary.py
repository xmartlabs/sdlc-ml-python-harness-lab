"""Best-effort LLM polish for imported case names and descriptions.

SWE-bench Pro problem statements arrive as raw issue-template mush ("## Title …
## Description … ## Steps to Reproduce 1. …"), which reads terribly in the case
list and detail panels. On import we make one small LLM call to produce a clean
2-4 sentence description; any failure (no transport configured, HTTP error,
empty answer) falls back to the heuristic summary — importing never breaks
because an LLM was unavailable.
"""

from __future__ import annotations

import os

from harness_lab_app import llm_transport
from harness_lab_app.config import read_env_file_value

_SYSTEM = (
    "You summarize GitHub issues for a software-engineering benchmark catalog. "
    "Reply with 2-4 plain sentences: what is broken or requested, and what a "
    "correct fix must achieve. No markdown headers, no lists, no code fences "
    "(inline backticks for identifiers are fine). Reply in English."
)


def polish_case_description(
    title: str, statement: str, model: str | None = None
) -> str | None:
    """One chat call → a clean multi-sentence description, or None on any failure."""
    # Summaries are a far easier task than audits — allow a dedicated (cheaper)
    # model before falling back to the auditor's.
    chosen = (
        model
        or os.environ.get("CASE_SUMMARY_MODEL")
        or read_env_file_value("CASE_SUMMARY_MODEL")
        or os.environ.get("AUDITOR_MODEL")
        or llm_transport.default_summary_model()
    )
    try:
        text = llm_transport.chat(
            chosen,
            _SYSTEM,
            f"Issue title: {title}\n\nIssue body:\n{statement[:8000]}",
            timeout=60,
            temperature=0.2,
            trace_kind="summary",
        ).strip()
    except llm_transport.TransportError:
        return None
    # Guard against fenced or degenerate replies — the fallback reads better.
    if not text or text.startswith("```") or len(text) < 40:
        return None
    return text[:900]
