"""Public-output sanitizer: field allowlist + secret/path detection (plan §16).

Two layers guard everything copied into a public issue:

1. ``build_public_issue`` renders ONLY an explicit allowlist of proposal fields —
   private run IDs, workspace paths, and evidence artifacts never reach it.
2. ``scan_secrets`` / ``assert_clean`` are a denylist backstop over the rendered
   text, catching tokens, key material, home paths, internal endpoints, ``.env``
   assignments, and private-repository links before publication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_lab_app.harness_lifecycle.models import Proposal


class SanitizationError(Exception):
    """Raised when public output would leak a secret or private path."""


@dataclass(frozen=True)
class SecretFinding:
    kind: str
    match: str


# Ordered (kind, pattern) denylist. Patterns target formats, not specific values.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_or_anthropic_key", re.compile(r"\bsk-[A-Za-z0-9._-]{16,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("home_path", re.compile(r"/(?:Users|home)/[^/\s]+")),
    (
        "loopback_or_private_ip",
        re.compile(
            r"\b(?:127\.0\.0\.1|localhost|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
        ),
    ),
    (
        "env_secret_assignment",
        re.compile(
            r"\b[A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*\s*=\s*\S+"
        ),
    ),
    ("private_repo_link", re.compile(r"github\.com/[\w.-]+/ai-agentic-harness-lab")),
)


def scan_secrets(text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for kind, pattern in _SECRET_PATTERNS:
        for m in pattern.finditer(text):
            findings.append(SecretFinding(kind=kind, match=m.group(0)))
    return findings


def assert_clean(text: str) -> None:
    findings = scan_secrets(text)
    if findings:
        kinds = ", ".join(sorted({f.kind for f in findings}))
        raise SanitizationError(f"Public output blocked; detected: {kinds}.")


def _render_success_criteria(proposal: Proposal) -> str:
    lines = []
    for c in proposal.validation.success_criteria:
        lines.append(f"  - {c.metric} {c.operator} {c.value}")
    return "\n".join(lines) if lines else "  - (none)"


def build_public_issue(proposal: Proposal) -> str:
    """Render the sanitized public issue body from allowlisted fields only.

    Private fields (run IDs, artifact paths, workspace, local paths) are never
    surfaced. Only aggregate condition hashes back the evidence. Raises
    ``SanitizationError`` if the rendered text still trips the secret scanner.
    """
    obs = proposal.observation
    artifacts = "\n".join(f"- {a}" for a in proposal.affected_artifacts) or "- (none)"
    cases = ", ".join(proposal.validation.suggested_cases) or "(none)"
    condition_hashes = (
        "\n".join(f"- {r.condition_hash}" for r in proposal.source.runs) or "- (none)"
    )

    body = f"""## Proposal {proposal.proposal_id}

**{proposal.title}**

### Observation
- failure_mode: {obs.failure_mode}
- expected_behavior: {obs.expected_behavior}
- evidence_summary: {obs.evidence_summary}

### Affected artifacts
{artifacts}

### Suggested change
{proposal.suggested_change.summary}

### Validation
- cases: {cases}
- success_criteria:
{_render_success_criteria(proposal)}

### Evidence (aggregate condition hashes)
{condition_hashes}

confidence: {proposal.confidence}
"""
    assert_clean(body)
    return body
