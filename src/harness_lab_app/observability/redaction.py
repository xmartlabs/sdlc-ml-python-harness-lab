"""Mask credentials before anything leaves the process.

Langfuse is an external service; a key pasted into a trace is a leaked key.
Run prompts and harness logs routinely contain `GATEWAY_TOKEN=...`,
`sk-...` and OAuth tokens, so every payload passes through :func:`redact`.

The credential patterns are the ones
:mod:`harness_lab_app.harness_lifecycle.sanitizer` already maintains, reached
through its public :func:`scan_secrets` API. Only the *credential* kinds are
masked: that scanner also flags home paths and loopback addresses, which are
correct to block in a public GitHub issue and pointless to strip from a trace
the operator is reading to debug their own container.

The `KEY=value` rule is deliberately blunt: inside a diff it also masks a
source constant such as `ADDITIONAL_PY2_KEYWORDS = [...]`. Losing one line of a
patch is the right side of that trade — the alternative is shipping a
`GATEWAY_TOKEN=` line to a service the lab does not control.
"""

from __future__ import annotations

import re

from harness_lab_app.harness_lifecycle.sanitizer import scan_secrets

MASK = "<redacted>"

# Kinds from sanitizer._SECRET_PATTERNS that are actual key material.
_CREDENTIAL_KINDS = frozenset(
    {
        "openai_or_anthropic_key",
        "github_token",
        "aws_access_key",
        "slack_token",
        "private_key_block",
        "env_secret_assignment",
    }
)

# Shapes the public-issue scanner has no reason to know about but that show up
# in harness logs: Authorization headers and the lab's own gateway tokens.
_EXTRA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9._-]{8,}"),
)

# `KEY=value` findings keep their key so the trace still shows *which* variable
# was set, with only the value masked.
_ASSIGNMENT_RE = re.compile(r"^([A-Z0-9_]+)\s*=\s*")


def _mask_for(kind: str, match: str) -> str:
    if kind == "env_secret_assignment":
        prefix = _ASSIGNMENT_RE.match(match)
        if prefix is not None:
            return f"{prefix.group(1)}={MASK}"
    return MASK


def redact(text: str) -> str:
    """Return `text` with every recognized credential replaced by ``<redacted>``."""
    if not text:
        return text
    replacements: dict[str, str] = {}
    for finding in scan_secrets(text):
        if finding.kind in _CREDENTIAL_KINDS:
            replacements[finding.match] = _mask_for(finding.kind, finding.match)
    # Longest first: a short match nested in a longer one must not corrupt it.
    for original in sorted(replacements, key=len, reverse=True):
        text = text.replace(original, replacements[original])
    for pattern in _EXTRA_PATTERNS:
        text = pattern.sub(MASK, text)
    return text
