"""Shared proposal-draft synthesis (LLM edge), reused by the CLI and the API.

Turns a run's behavioral audit into a structured, sanitized public issue: the LLM
produces a draft JSON, which becomes a :class:`Proposal`, which the sanitizer
renders into copy-paste issue markdown. The gateway is an injectable callable so
the pure assembly stays testable.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from harness_lab_app.harness_lifecycle import (
    issue_form,
    metrics,
    proposals,
    sanitizer,
)
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

# The lab fails closed on any success-criterion metric it cannot compute
# (gates.evaluate_criterion), so the draft must draw only from this catalog.
_METRIC_CATALOG = "; ".join(
    f"{name} ({desc})" for name, desc in metrics.MEASURABLE_METRICS.items()
)

SYSTEM_PROMPT = (
    "You convert a single agent-run behavioral audit into ONE governed harness "
    "improvement proposal. Output ONLY a JSON object with keys: title (str), "
    "observation {failure_mode, expected_behavior, evidence_summary}, "
    "affected_artifacts (list of exact repo paths under .github/ or adapters/"
    "templates), suggested_change (str), suggested_cases (list of case ids), "
    "success_criteria (list of {metric, operator in [lte,gte,eq,lt,gt], value}), "
    "confidence in [low,medium,high]. "
    "Each success_criteria.metric MUST be one of the lab's measurable metrics: "
    f"{_METRIC_CATALOG}. Do NOT invent metric names — the lab automatically fails "
    "any criterion whose metric it cannot compute, which would keep the cycle from "
    "ever closing. Map behavioral intent to the closest measurable metric (e.g. run "
    "length or number of turns -> steps; repeated, redundant, or thrashing actions -> "
    "repeated_command_max; needing manual help -> interventions). Express any goal "
    "that none of these metrics can capture (e.g. whether governance docs were read, "
    "whether a formal verify gate was invoked) only as prose inside suggested_change, "
    "never as a success_criteria entry. "
    "Describe aggregate behavior only; never include secrets, tokens, local paths, "
    "or raw logs."
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


def synthesize_proposal(
    run_dir: Path,
    *,
    repository: str,
    created_at: datetime,
    model: str = DEFAULT_MODEL,
    gateway: Callable[[str, str, str], str] | None = None,
) -> Proposal:
    """Run audit → structured :class:`Proposal` (LLM call, nothing written).

    ``created_at`` is injected so callers control the (unwritten) placeholder id's
    year.
    """
    gateway = gateway or load_gateway()
    evidence = proposals.extract_run_evidence(run_dir)
    draft = synthesize_draft(run_dir, model, gateway)
    return draft_to_proposal(
        draft,
        proposal_id=f"HEP-{created_at.year}-000",
        created_at=created_at,
        source=ProposalSource(repository=repository, runs=[evidence]),
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

    Raises ``sanitizer.SanitizationError`` if the issue would leak.
    """
    proposal = synthesize_proposal(
        run_dir,
        repository=repository,
        created_at=created_at,
        model=model,
        gateway=gateway,
    )
    return sanitizer.build_public_issue(proposal)


GROUP_SYSTEM_PROMPT = (
    "You consolidate MULTIPLE harness improvement proposals, drawn from several "
    "agent runs, into ONE governed harness improvement proposal. Merge "
    "overlapping suggestions, keep the highest-signal changes, and unify the "
    "affected artifacts. Output ONLY a JSON object with keys: title (str), "
    "observation {failure_mode, expected_behavior, evidence_summary}, "
    "affected_artifacts (list of exact repo paths under .github/ or adapters/"
    "templates), suggested_change (str), suggested_cases (list of case ids), "
    "success_criteria (list of {metric, operator in [lte,gte,eq,lt,gt], value}), "
    "confidence in [low,medium,high]. "
    "The user message opens with a 'Target surface' directive derived from the "
    "harness that produced these runs — your affected_artifacts MUST edit exactly "
    "that surface (a single harness's adapter, or the shared .github/ layer when "
    "the evidence spans harnesses). "
    "Each success_criteria.metric MUST be one of the lab's measurable metrics: "
    f"{_METRIC_CATALOG}. Do NOT invent metric names — the lab automatically fails "
    "any criterion whose metric it cannot compute. Express any goal none of these "
    "metrics can capture only as prose inside suggested_change. "
    "Describe aggregate behavior only; never include run identifiers, case-run "
    "codes, secrets, tokens, local filesystem paths, hostnames, or raw logs — "
    "the input proposals are private and unsanitized, your output is public."
)

# Per-proposal body / per-run audit excerpt clip, and the same total clip as
# synthesize_draft — keeps the prompt bounded for large selections.
_GROUP_ITEM_CLIP = 6000
_GROUP_TOTAL_CLIP = 40000

# Which downstream-template surface an improvement targets, keyed by the harness
# that produced it. Each harness ships its own adapter files, so a fix proven
# under one harness belongs in that harness's adapter; a fix that recurs across
# harnesses is harness-independent and belongs in the shared governed layer that
# syncs out to all of them. Generic tool names + repo paths — never private data.
_HARNESS_SURFACE = {
    "claude": "the claude adapter (CLAUDE.md and .claude/ skills/subagents)",
    "opencode": "the opencode adapter (AGENTS.md and opencode.json)",
    "codex": "the codex adapter (AGENTS.md / Codex config)",
    "antigravity": "the antigravity adapter (.agents/ workspace mirror)",
}
_SHARED_SURFACE = "the governed .github/ layer (synced to every harness adapter)"


def _is_self_hosted(model: str) -> bool:
    """A self-hosted/local executor is routed through the lab gateway (``gateway/…``).

    Native cloud harnesses pass bare vendor ids (``claude-opus-4-8``); only the
    self-hosted backends (LM Studio, NIM, …) carry the gateway prefix. Heuristic,
    used only to attach an advisory note.
    """
    return model.strip().lower().startswith("gateway/")


def harness_target_surface(items: list[dict]) -> dict:
    """Deterministic target surface for a proposal set, derived from its harness.

    Returns ``{harnesses, surface, self_hosted, note}``: one distinct harness →
    that harness's adapter; several (or none recorded) → the shared governed
    layer. Pure and secret-free (harness names and repo paths only).
    """
    harnesses = sorted(
        {(item.get("harness") or "").strip().lower() for item in items} - {""}
    )
    self_hosted = any(
        _is_self_hosted(item.get("executor_model") or "") for item in items
    )
    surface = (
        _HARNESS_SURFACE.get(harnesses[0], _SHARED_SURFACE)
        if len(harnesses) == 1
        else _SHARED_SURFACE
    )
    note = (
        "At least one run used a self-hosted/local model — weaker models benefit "
        "from more explicit, front-loaded governance in that surface."
        if self_hosted
        else ""
    )
    return {
        "harnesses": harnesses,
        "surface": surface,
        "self_hosted": self_hosted,
        "note": note,
    }


def render_target_surface(scope: dict) -> str:
    """A deterministic markdown block naming the surface an issue should edit."""
    who = ", ".join(scope["harnesses"]) if scope["harnesses"] else "unknown"
    lines = [
        "## Target harness surface",
        "",
        f"- Harness(es) observed: **{who}**",
        f"- Apply the change in: **{scope['surface']}**",
    ]
    if scope["note"]:
        lines.append(f"- Note: {scope['note']}")
    return "\n".join(lines)


def _run_id_pseudonyms(items: list[dict], audits: dict[str, str]) -> dict[str, str]:
    """Map each known run id to a stable ``run-N`` pseudonym (sorted for order)."""
    run_ids: set[str] = {str(item["run_id"]) for item in items if item.get("run_id")}
    run_ids |= set(audits)
    return {
        run_id: f"run-{index}" for index, run_id in enumerate(sorted(run_ids), start=1)
    }


def _mask_run_ids(text: str, pseudonyms: dict[str, str]) -> str:
    """Replace whole-word run-id occurrences with their pseudonyms.

    Longest id first so an id that is a prefix of another masks cleanly; word
    boundaries keep single-char ids from corrupting surrounding prose.
    """
    for run_id in sorted(pseudonyms, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(run_id)}\b", pseudonyms[run_id], text)
    return text


def build_group_user_message(items: list[dict], audits: dict[str, str]) -> str:
    """Render selected proposals (+ optional per-run audit excerpts) as one prompt.

    ``items`` are unsanitized proposal snapshots ({title, kind, target, body,
    case, harness, executor_model, run_id}); ``audits`` maps run_id -> audit.md
    text. Run ids ride in via the bodies and audit excerpts, so they are masked
    to ``run-N`` pseudonyms before the model sees them — the model cannot echo a
    private id it never received, which turns the output backstop from a routine
    blocker into a rare safety net. Pure assembly, testable without a gateway.
    """
    pseudonyms = _run_id_pseudonyms(items, audits)
    scope = harness_target_surface(items)
    directive = (
        f"Target surface — your affected_artifacts MUST edit {scope['surface']}."
    )
    if scope["note"]:
        directive = f"{directive} {scope['note']}"
    blocks: list[str] = [
        "Consolidate the following improvement proposals into one proposal.",
        directive,
    ]
    for index, item in enumerate(items, start=1):
        lines = [f"## Proposal {index}: {item.get('title', '')}"]
        for label, key in (
            ("Kind", "kind"),
            ("Target", "target"),
            ("Case", "case"),
            ("Harness", "harness"),
            ("Executor model", "executor_model"),
        ):
            value = item.get(key)
            if value:
                lines.append(f"- {label}: {value}")
        body = (item.get("body") or "")[:_GROUP_ITEM_CLIP]
        if body:
            lines.append("")
            lines.append(body)
        blocks.append("\n".join(lines))
    for run_id in sorted(audits):
        excerpt = audits[run_id][:_GROUP_ITEM_CLIP]
        if excerpt:
            blocks.append(f"## Audit excerpt ({pseudonyms[run_id]})\n\n{excerpt}")
    return _mask_run_ids("\n\n".join(blocks), pseudonyms)[:_GROUP_TOTAL_CLIP]


def synthesize_group_issue(
    items: list[dict],
    run_dirs: list[Path],
    *,
    repository: str,
    created_at: datetime,
    template: str = issue_form.DEFAULT_TEMPLATE,
    model: str = DEFAULT_MODEL,
    gateway: Callable[[str, str, str], str] | None = None,
) -> tuple[str, issue_form.IssueForm]:
    """N selected proposals → ONE combined sanitized issue (LLM call, unwritten).

    Evidence is one condition-hash entry per run dir (runs missing metadata.json
    degrade gracefully). Besides the allowlist render and denylist scan, a
    deterministic backstop rejects any output echoing a selected run id — run
    ids are not in the sanitizer denylist, and the input bodies routinely
    contain them. Raises ``sanitizer.SanitizationError`` on any leak.
    """
    gateway = gateway or load_gateway()
    evidence_list = []
    audits: dict[str, str] = {}
    for run_dir in run_dirs:
        with contextlib.suppress(FileNotFoundError):
            evidence_list.append(proposals.extract_run_evidence(run_dir))
        audit_path = run_dir / "audit.md"
        if audit_path.exists():
            audits[run_dir.name] = audit_path.read_text(encoding="utf-8")
    user_message = build_group_user_message(items, audits)
    draft = parse_draft(gateway(model, GROUP_SYSTEM_PROMPT, user_message))
    proposal = draft_to_proposal(
        draft,
        proposal_id=f"HEP-{created_at.year}-000",
        created_at=created_at,
        source=ProposalSource(repository=repository, runs=evidence_list),
    )
    # Deterministic scope header from the selection's harness (secret-free, so it
    # is safe to prepend after sanitization) — the authoritative statement of
    # which surface to edit, independent of what the model chose.
    markdown = (
        f"{render_target_surface(harness_target_surface(items))}\n\n"
        f"{sanitizer.build_public_issue(proposal)}"
    )
    form = issue_form.build_issue_form(
        proposal, repository=repository, template=template
    )
    run_ids = {run_dir.name for run_dir in run_dirs}
    haystacks = [markdown, form.title, *(field.value for field in form.fields)]
    for run_id in run_ids:
        if any(run_id in haystack for haystack in haystacks):
            raise sanitizer.SanitizationError("combined issue echoed a private run id")
    return markdown, form


def synthesize_issue(
    run_dir: Path,
    *,
    repository: str,
    created_at: datetime,
    template: str = issue_form.DEFAULT_TEMPLATE,
    model: str = DEFAULT_MODEL,
    gateway: Callable[[str, str, str], str] | None = None,
) -> tuple[str, issue_form.IssueForm]:
    """One LLM call → (copy-paste markdown, prefilled GitHub issue-form mapping).

    The form maps the proposal onto the ml-python-base harness-improvement issue
    form and carries a prefilled new-issue URL. Raises
    ``sanitizer.SanitizationError`` if either output would leak.
    """
    proposal = synthesize_proposal(
        run_dir,
        repository=repository,
        created_at=created_at,
        model=model,
        gateway=gateway,
    )
    markdown = sanitizer.build_public_issue(proposal)
    form = issue_form.build_issue_form(
        proposal, repository=repository, template=template
    )
    return markdown, form
