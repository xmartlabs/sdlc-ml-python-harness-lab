"""Tests for the GitHub issue-form mapping + prefilled URL (issue_form)."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from harness_lab_app.harness_lifecycle import issue_form, sanitizer
from harness_lab_app.harness_lifecycle.models import (
    Observation,
    Proposal,
    ProposalSource,
    ProposalValidation,
    RunArtifacts,
    RunEvidence,
    SuccessCriterion,
    SuggestedChange,
)

FIXED = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


def _proposal(
    *,
    artifacts: list[str],
    change: str = "Add an explicit retry budget and hypothesis reset rule.",
) -> Proposal:
    return Proposal(
        proposal_id="HEP-2026-000",
        title="Require a retry budget before re-reading",
        created_at=FIXED,
        source=ProposalSource(
            repository="marcosdh1987/ml-python-base",
            runs=[
                RunEvidence(
                    run_id="dbg-K-001",
                    condition_hash="sha256:abc123",
                    artifacts=RunArtifacts(audit="a", metadata_sha256="b"),
                )
            ],
        ),
        observation=Observation(
            failure_mode="Agent re-read the same file and churned hypotheses.",
            expected_behavior="Reset the hypothesis after a retry budget.",
            evidence_summary="Seen across one run.",
        ),
        affected_artifacts=artifacts,
        suggested_change=SuggestedChange(summary=change),
        validation=ProposalValidation(
            suggested_cases=["systematic-debugging-v1"],
            success_criteria=[
                SuccessCriterion(
                    metric="repeated_equivalent_command_max", operator="lte", value=2
                )
            ],
        ),
        confidence="high",
    )


def test_governance_channel_for_docs_only() -> None:
    form = issue_form.build_issue_form(
        _proposal(artifacts=[".github/architecture.md", ".github/standards.md"]),
        repository="marcosdh1987/ml-python-base",
    )
    channel = next(f for f in form.fields if f.id == "channel")
    assert channel.value == "governance"
    assert channel.recommended and channel.reason


def test_platform_channel_when_machinery_touched() -> None:
    form = issue_form.build_issue_form(
        _proposal(artifacts=[".github/standards.md", "Makefile"]),
        repository="marcosdh1987/ml-python-base",
    )
    channel = next(f for f in form.fields if f.id == "channel")
    assert channel.value == "platform"
    assert "Makefile" in channel.reason


def test_semver_minor_on_additive_wording() -> None:
    form = issue_form.build_issue_form(
        _proposal(
            artifacts=[".github/standards.md"], change="Add a new preflight gate."
        ),
        repository="marcosdh1987/ml-python-base",
    )
    assert next(f for f in form.fields if f.id == "semver").value == "semver:minor"


def test_semver_major_on_removal_wording() -> None:
    form = issue_form.build_issue_form(
        _proposal(
            artifacts=[".github/standards.md"], change="Remove the legacy retry rule."
        ),
        repository="marcosdh1987/ml-python-base",
    )
    assert next(f for f in form.fields if f.id == "semver").value == "semver:major"


def test_url_is_prefilled_and_decodes_back() -> None:
    form = issue_form.build_issue_form(
        _proposal(artifacts=[".github/standards.md"]),
        repository="marcosdh1987/ml-python-base",
    )
    parsed = urlparse(form.url)
    assert parsed.path == "/marcosdh1987/ml-python-base/issues/new"
    q = parse_qs(parsed.query)
    assert q["template"] == ["harness-improvement.yml"]
    assert q["title"][0].startswith("[HEP] ")
    assert q["proposal_id"] == ["HEP-2026-000"]
    assert q["channel"] == ["governance"]
    assert "failure_mode:" in q["observation"][0]


def test_leaking_field_is_blocked() -> None:
    leaky = _proposal(
        artifacts=[".github/standards.md"],
        change="Read /Users/marcos/secrets every step.",
    )
    with pytest.raises(sanitizer.SanitizationError):
        issue_form.build_issue_form(leaky, repository="marcosdh1987/ml-python-base")
