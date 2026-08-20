"""Tests for shared proposal-draft synthesis (drafting), with a fake gateway."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from harness_lab_app.harness_lifecycle import drafting, metrics

FIXED = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)

_DRAFT = {
    "title": "Add a retry budget to systematic debugging",
    "observation": {
        "failure_mode": "Agent re-read the same file and churned hypotheses.",
        "expected_behavior": "Reset the hypothesis after a retry budget.",
        "evidence_summary": "Seen across the debugging steps of one run.",
    },
    "affected_artifacts": [".github/skills/systematic_debugging.md"],
    "suggested_change": "Add an explicit retry budget and hypothesis reset rule.",
    "suggested_cases": ["systematic-debugging-v1"],
    "success_criteria": [
        {"metric": "repeated_command_max", "operator": "lte", "value": 2}
    ],
    "confidence": "high",
}


def _run_dir(root: Path) -> Path:
    d = root / "data" / "runs" / "dbg-K-001"
    d.mkdir(parents=True)
    # A private path lives in the audit; it must never reach the public issue.
    (d / "audit.md").write_text(
        "# Audit\nRe-read /Users/marcos/x every step.\n", encoding="utf-8"
    )
    (d / "metadata.json").write_text(
        json.dumps({"run_id": "dbg-K-001", "condition_hash": "sha256:abc"}),
        encoding="utf-8",
    )
    return d


def test_draft_to_proposal() -> None:
    from harness_lab_app.harness_lifecycle.models import ProposalSource

    proposal = drafting.draft_to_proposal(
        _DRAFT,
        proposal_id="HEP-2026-014",
        created_at=FIXED,
        source=ProposalSource(repository="xmartlabs/sdlc-ml-python-harness-lab"),
    )
    assert proposal.title.startswith("Add a retry budget")
    assert proposal.validation.success_criteria[0].metric == ("repeated_command_max")


def test_system_prompt_constrains_metrics_to_the_catalog() -> None:
    # The draft LLM must only emit metrics the lab can compute; keep the prompt and
    # the canonical catalog coupled so an off-catalog metric never ships a gate that
    # fails closed forever.
    for name in metrics.MEASURABLE_METRICS:
        assert name in drafting.SYSTEM_PROMPT


def test_synthesize_issue_markdown_is_sanitized(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    markdown = drafting.synthesize_issue_markdown(
        run_dir,
        repository="xmartlabs/sdlc-ml-python-base",
        created_at=FIXED,
        gateway=lambda *_args: json.dumps(_DRAFT),
    )
    # Allowlisted content is present...
    assert "Reset the hypothesis after a retry budget." in markdown
    assert ".github/skills/systematic_debugging.md" in markdown
    # ...and private evidence (run id / local paths) is not surfaced.
    assert "dbg-K-001" not in markdown
    assert "/Users/marcos" not in markdown
