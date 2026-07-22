"""Best-effort LLM polish for imported case names and descriptions.

SWE-bench Pro problem statements arrive as raw issue-template mush ("## Title …
## Description … ## Steps to Reproduce 1. …"), which reads terribly in the case
list and detail panels. On import we make one small gateway call to produce a
clean 2-4 sentence description; any failure (no gateway configured, HTTP error,
empty answer) falls back to the heuristic summary — importing never breaks
because an LLM was unavailable.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from harness_lab_app.config import get_settings, read_env_file_value
from harness_lab_app.executors import rewrite_localhost
from harness_lab_app.gateway_models import GATEWAY_USER_AGENT, normalize_model_name

# Nano tier: summaries are extraction, not reasoning ($0.20/$1.25 per M).
DEFAULT_SUMMARY_MODEL = "gpt-5.4-nano"

_SYSTEM = (
    "You summarize GitHub issues for a software-engineering benchmark catalog. "
    "Reply with 2-4 plain sentences: what is broken or requested, and what a "
    "correct fix must achieve. No markdown headers, no lists, no code fences "
    "(inline backticks for identifiers are fine). Reply in English."
)


def _gateway() -> tuple[str, str] | None:
    base = (
        os.environ.get("GATEWAY_BASE_URL")
        or read_env_file_value("GATEWAY_BASE_URL")
        or ""
    ).strip()
    token = (
        os.environ.get("GATEWAY_TOKEN") or read_env_file_value("GATEWAY_TOKEN") or ""
    ).strip()
    if not base or not token:
        return None
    # Inside a container "localhost" is the container itself.
    if Path("/.dockerenv").exists():
        base = rewrite_localhost(base, get_settings().docker_host_alias)
    return base.rstrip("/"), token


def polish_case_description(
    title: str, statement: str, model: str | None = None
) -> str | None:
    """One chat call → a clean multi-sentence description, or None on any failure."""
    gateway = _gateway()
    if gateway is None:
        return None
    base, token = gateway
    # Summaries are a far easier task than audits — allow a dedicated (cheaper)
    # model before falling back to the auditor's.
    chosen = (
        model
        or os.environ.get("CASE_SUMMARY_MODEL")
        or read_env_file_value("CASE_SUMMARY_MODEL")
        or os.environ.get("AUDITOR_MODEL")
        or DEFAULT_SUMMARY_MODEL
    )
    payload = {
        "model": normalize_model_name(chosen),
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": f"Issue title: {title}\n\nIssue body:\n{statement[:8000]}",
            },
        ],
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": GATEWAY_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:  # nosec B310
            data = json.loads(resp.read())
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        OSError,
    ):
        return None
    # Guard against fenced or degenerate replies — the fallback reads better.
    if not text or text.startswith("```") or len(text) < 40:
        return None
    return text[:900]
