"""Map a sanitized :class:`Proposal` onto the ml-python-base 'Harness improvement'
GitHub issue form and build a prefilled new-issue URL.

The form lives at ``.github/ISSUE_TEMPLATE/harness-improvement.yml`` in
ml-python-base; its field ``id``s are the keys used both for the copy-paste
mapping and for GitHub's query-parameter prefill. The lab draft does not decide
the two governance dropdowns (change channel and SemVer impact), so this module
derives a *recommended* value for each with a small, documented heuristic. The
human confirms both before creating the issue — every recommendation carries a
one-line reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from harness_lab_app.harness_lifecycle import sanitizer

if TYPE_CHECKING:
    from harness_lab_app.harness_lifecycle.models import Proposal

DEFAULT_TEMPLATE = "harness-improvement.yml"

# Affected-artifact substrings that mean the change touches platform machinery
# (sync engine, build, packaging, CI) rather than safe-to-sync governance content.
_PLATFORM_HINTS = (
    "Makefile",
    "pyproject",
    "setup.py",
    "setup.cfg",
    "registry.toml",
    "/workflows/",
    "skills_sync",
    "renderer.py",
    "packaging",
)

# Keyword signals for the SemVer dropdown, checked MAJOR → MINOR → PATCH.
_MAJOR_HINTS = (
    "remove",
    "removal",
    "rename",
    "delete",
    "drop ",
    "break",
    "breaking",
    "incompatible",
    "restructure",
    "relocat",
    "move ",
)
_MINOR_HINTS = (
    "add",
    "additive",
    "new ",
    "introduce",
    "require",
    "enforce",
    "gate",
    "checklist",
    "preflight",
    "extend",
    "augment",
)


@dataclass(frozen=True)
class FormField:
    id: str
    label: str
    value: str
    kind: str  # "input" | "textarea" | "dropdown"
    recommended: bool = False
    reason: str = ""


@dataclass(frozen=True)
class IssueForm:
    repository: str
    template: str
    title: str
    fields: list[FormField]
    url: str

    def to_dict(self) -> dict:
        return {
            "repository": self.repository,
            "template": self.template,
            "title": self.title,
            "url": self.url,
            "fields": [
                {
                    "id": f.id,
                    "label": f.label,
                    "value": f.value,
                    "kind": f.kind,
                    "recommended": f.recommended,
                    "reason": f.reason,
                }
                for f in self.fields
            ],
        }


def recommend_channel(proposal: Proposal) -> tuple[str, str]:
    """Governance unless an affected artifact touches platform machinery."""
    hits = [
        a for a in proposal.affected_artifacts if any(h in a for h in _PLATFORM_HINTS)
    ]
    if hits:
        return (
            "platform",
            f"Touches platform machinery ({', '.join(hits)}); ship it as a separate "
            "reviewed PR, not through the governance sync.",
        )
    return (
        "governance",
        "All affected artifacts are governance content (skills/agents/docs/adapter "
        "templates) — safe to sync to consumers.",
    )


def recommend_semver(proposal: Proposal) -> tuple[str, str]:
    """Escalate from PATCH on additive/removal wording in the proposed change."""
    text = " ".join(
        [
            proposal.suggested_change.summary,
            proposal.observation.expected_behavior,
            proposal.observation.failure_mode,
        ]
    ).lower()
    if any(h in text for h in _MAJOR_HINTS):
        return ("semver:major", "The change reads as a removal/rename/contract break.")
    if any(h in text for h in _MINOR_HINTS):
        return (
            "semver:minor",
            "The change adds a new compatible rule/skill/agent/gate (additive).",
        )
    return ("semver:patch", "The change reads as a clarification of existing guidance.")


def _render_observation(proposal: Proposal) -> str:
    o = proposal.observation
    return (
        f"failure_mode: {o.failure_mode}\n"
        f"expected_behavior: {o.expected_behavior}\n"
        f"evidence_summary: {o.evidence_summary}"
    )


def _render_affected(proposal: Proposal) -> str:
    return "\n".join(f"- {a}" for a in proposal.affected_artifacts) or "- (none)"


def _render_validation(proposal: Proposal) -> str:
    lines = ["cases:"]
    lines += [f"  - {c}" for c in proposal.validation.suggested_cases] or ["  - (none)"]
    lines.append("success_criteria:")
    if proposal.validation.success_criteria:
        for c in proposal.validation.success_criteria:
            lines.append(f"  - metric: {c.metric}")
            lines.append(f"    operator: {c.operator}")
            lines.append(f"    value: {c.value}")
    else:
        lines.append("  - (none)")
    return "\n".join(lines)


def _render_source_runs(proposal: Proposal) -> str:
    hashes = [r.condition_hash for r in proposal.source.runs]
    n = len(hashes)
    return (", ".join(hashes) or "(none)") + f" (x{n} run{'s' if n != 1 else ''})"


def _title(proposal: Proposal) -> str:
    t = proposal.title.strip()
    return t if t.startswith("[HEP]") else f"[HEP] {t}"


def build_issue_form(
    proposal: Proposal, *, repository: str, template: str = DEFAULT_TEMPLATE
) -> IssueForm:
    """Build the field mapping + prefilled new-issue URL for the harness form.

    Runs the same secret scan as the public issue body over every emitted value,
    so a leaking field can never reach the URL. Raises
    :class:`sanitizer.SanitizationError` on a detected leak.
    """
    channel, channel_reason = recommend_channel(proposal)
    semver, semver_reason = recommend_semver(proposal)
    title = _title(proposal)

    fields = [
        FormField("proposal_id", "Proposal ID", proposal.proposal_id, "input"),
        FormField(
            "channel", "Change channel", channel, "dropdown", True, channel_reason
        ),
        FormField(
            "semver", "Expected SemVer impact", semver, "dropdown", True, semver_reason
        ),
        FormField(
            "observation", "Observation", _render_observation(proposal), "textarea"
        ),
        FormField(
            "affected_artifacts",
            "Affected artifacts",
            _render_affected(proposal),
            "textarea",
        ),
        FormField(
            "suggested_change",
            "Suggested change",
            proposal.suggested_change.summary,
            "textarea",
        ),
        FormField(
            "validation",
            "Validation criteria",
            _render_validation(proposal),
            "textarea",
        ),
        FormField(
            "source_runs",
            "Source run condition hashes",
            _render_source_runs(proposal),
            "input",
        ),
    ]

    # Secret backstop over everything we are about to publish (title + all values).
    sanitizer.assert_clean("\n".join([title, *(f.value for f in fields)]))

    query = [("template", template), ("title", title)]
    query += [(f.id, f.value) for f in fields]
    qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in query)
    url = f"https://github.com/{repository}/issues/new?{qs}"

    return IssueForm(
        repository=repository, template=template, title=title, fields=fields, url=url
    )
