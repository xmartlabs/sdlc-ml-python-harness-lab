"""Shared proposal-draft synthesis (LLM edge), reused by the CLI and the API.

Turns a run's behavioral audit into a structured, sanitized public issue: the LLM
produces a draft JSON, which becomes a :class:`Proposal`, which the sanitizer
renders into copy-paste issue markdown. The gateway is an injectable callable so
the pure assembly stays testable.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from harness_lab_app.harness_lifecycle import proposals, sanitizer
from harness_lab_app.harness_lifecycle.models import (
    Observation,
    Proposal,
    ProposalSource,
    ProposalValidation,
    SuccessCriterion,
    SuggestedChange,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL = "gpt-5.4-mini"

SYSTEM_PROMPT = (
    "You convert a single agent-run behavioral audit into ONE governed harness "
    "improvement proposal. Output ONLY a JSON object with keys: title (str), "
    "observation {failure_mode, expected_behavior, evidence_summary}, "
    "affected_artifacts (list of exact repo paths under .github/ or adapters/"
    "templates), suggested_change (str), suggested_cases (list of case ids), "
    "success_criteria (list of {metric, operator in [lte,gte,eq,lt,gt], value}), "
    "confidence in [low,medium,high]. Describe aggregate behavior only; never "
    "include secrets, tokens, local paths, or raw logs."
)


def load_gateway() -> Callable[[str, str, str], str]:
    """Lazily obtain the LLM gateway from scripts/ (infrastructure edge)."""
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from bench_audit import call_gateway

    return call_gateway


def parse_draft(raw: str) -> dict:
    """Tolerantly extract the JSON object from an LLM response."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM response contained no JSON object.")
    return json.loads(text[start : end + 1])


def synthesize_draft(
    run_dir: Path, model: str, gateway: Callable[[str, str, str], str]
) -> dict:
    audit = (run_dir / "audit.md").read_text(encoding="utf-8")[:40000]
    return parse_draft(gateway(model, SYSTEM_PROMPT, audit))


def draft_to_proposal(
    draft: dict, *, proposal_id: str, created_at: datetime, source: ProposalSource
) -> Proposal:
    return proposals.build_proposal(
        proposal_id=proposal_id,
        title=draft["title"],
        created_at=created_at,
        source=source,
        observation=Observation(**draft["observation"]),
        affected_artifacts=list(draft.get("affected_artifacts", [])),
        suggested_change=SuggestedChange(summary=draft["suggested_change"]),
        validation=ProposalValidation(
            suggested_cases=list(draft.get("suggested_cases", [])),
            success_criteria=[
                SuccessCriterion(**c) for c in draft.get("success_criteria", [])
            ],
        ),
        confidence=draft.get("confidence", "medium"),
    )


def synthesize_issue_markdown(
    run_dir: Path,
    *,
    repository: str,
    created_at: datetime,
    model: str = DEFAULT_MODEL,
    gateway: Callable[[str, str, str], str] | None = None,
) -> str:
    """Full path: run audit → sanitized public issue markdown (no file written).

    ``created_at`` is injected so callers control the (unwritten) placeholder id's
    year. Raises ``sanitizer.SanitizationError`` if the issue would leak.
    """
    gateway = gateway or load_gateway()
    evidence = proposals.extract_run_evidence(run_dir)
    draft = synthesize_draft(run_dir, model, gateway)
    proposal = draft_to_proposal(
        draft,
        proposal_id=f"HEP-{created_at.year}-000",
        created_at=created_at,
        source=ProposalSource(repository=repository, runs=[evidence]),
    )
    return sanitizer.build_public_issue(proposal)
