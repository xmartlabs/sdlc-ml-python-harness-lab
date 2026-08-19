"""Tests for the public-output sanitizer (harness_lifecycle.sanitizer).

Enforces plan §16: a public field allowlist plus secret/path detection covering
tokens, key formats, internal endpoints, home paths, .env content, and private
repository links. The scanner blocks leakage; the issue builder surfaces only
allowlisted fields.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from harness_lab_app.harness_lifecycle import sanitizer
from harness_lab_app.harness_lifecycle.models import (
    Observation,
    Proposal,
    ProposalSource,
    RunArtifacts,
    RunEvidence,
    SuggestedChange,
)

FIXED = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "text",
    [
        "token sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "AWS AKIAIOSFODNN7EXAMPLE key",
        "home path /Users/marcossoto/secret/run.log",
        "home path /home/marcos/private",
        "endpoint http://localhost:8000/internal",
        "endpoint http://127.0.0.1:6379",
        "GATEWAY_TOKEN=supersecretvalue123",
        "-----BEGIN PRIVATE KEY-----",
    ],
)
def test_scan_detects_secrets(text: str) -> None:
    findings = sanitizer.scan_secrets(text)
    assert findings, f"expected a finding in: {text}"


def test_scan_clean_text() -> None:
    assert sanitizer.scan_secrets("A normal aggregate observation about retries.") == []


def test_assert_clean_raises_on_secret() -> None:
    with pytest.raises(sanitizer.SanitizationError):
        sanitizer.assert_clean("path /Users/marcossoto/x")


def test_assert_clean_passes_on_safe_text() -> None:
    sanitizer.assert_clean("Reframe the hypothesis after the retry budget.")


def _proposal_with_secret_in_private_field() -> Proposal:
    # A leaked home path hides in a PRIVATE field (source runs). The public issue
    # must not surface it, and the builder must still pass the secret scan.
    return Proposal(
        proposal_id="HEP-2026-020",
        title="Add retry budget",
        created_at=FIXED,
        source=ProposalSource(
            repository="marcosdh1987/ai-agentic-harness-lab",
            runs=[
                RunEvidence(
                    run_id="dbg-K-001",
                    condition_hash="sha256:deadbeef",
                    artifacts=RunArtifacts(
                        audit="/Users/marcossoto/data/runs/dbg-K-001/audit.md",
                        metadata_sha256="sha256:cafe",
                    ),
                )
            ],
        ),
        observation=Observation(
            failure_mode="Repeated an equivalent failing command.",
            expected_behavior="Reframe the hypothesis after the retry budget.",
            evidence_summary="Seen in two runs.",
        ),
        affected_artifacts=[".github/skills/systematic_debugging.md"],
        suggested_change=SuggestedChange(summary="Add a retry budget."),
        confidence="high",
    )


def test_public_issue_omits_private_fields_and_passes_scan() -> None:
    p = _proposal_with_secret_in_private_field()
    issue = sanitizer.build_public_issue(p)
    # Private run path is never surfaced.
    assert "/Users/marcossoto" not in issue
    assert "dbg-K-001" not in issue
    # Allowlisted content is present.
    assert "Reframe the hypothesis" in issue
    assert ".github/skills/systematic_debugging.md" in issue
    # And the rendered issue passes the secret scan.
    sanitizer.assert_clean(issue)


def test_public_issue_raises_when_allowlisted_field_leaks() -> None:
    p = _proposal_with_secret_in_private_field()
    # Inject a secret into a PUBLIC (allowlisted) field.
    p.suggested_change.summary = "Set GATEWAY_TOKEN=supersecretvalue123 in env"
    with pytest.raises(sanitizer.SanitizationError):
        sanitizer.build_public_issue(p)


def test_public_issue_masks_case_ids_and_declares_lab_scope() -> None:
    p = _proposal_with_secret_in_private_field()
    p.validation.suggested_cases = [
        "instance_internetarchive__openlibrary-abc123-K-004",
        "instance_ansible__ansible-def456-K-004",
    ]
    issue = sanitizer.build_public_issue(p)
    assert "instance_internetarchive__openlibrary-abc123-K-004" not in issue
    assert "case-1, case-2" in issue
    assert sanitizer.VALIDATION_SCOPE_NOTE in issue
