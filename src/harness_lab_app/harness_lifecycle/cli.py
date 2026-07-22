"""Command-line entry point for the harness lifecycle (Phase 3: proposals).

Two subcommands:

* ``proposal --run <id>`` — synthesize a versioned HEP proposal draft from a run's
  audit, write it under ``data/harness-proposals/``, and print a sanitized public
  issue preview. ``--dry-run`` writes nothing.
* ``proposal-issue --proposal <id>`` — render the sanitized issue and, with
  ``--create``, file it in the template repo idempotently, linking it back.

The LLM synthesis and ``gh`` calls are the only infrastructure edges here; the
domain logic lives in :mod:`.proposals` and :mod:`.sanitizer`. All subprocess and
``gh`` calls pass argument arrays (never a shell string).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from harness_lab_app.harness_lifecycle import (
    errors,
    manifests,
    proposals,
    releases,
    sanitizer,
    sync,
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

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = REPO_ROOT / "data" / "runs"
LAB_REPOSITORY = "marcosdh1987/ai-agentic-harness-lab"
TEMPLATE_REPOSITORY = "marcosdh1987/ml-python-base"
DEFAULT_MODEL = "gpt-5.4-mini"

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_PRECONDITION = 3
EXIT_SANITIZATION = 5
EXIT_MISSING = 7

_SYSTEM_PROMPT = (
    "You convert a single agent-run behavioral audit into ONE governed harness "
    "improvement proposal. Output ONLY a JSON object with keys: title (str), "
    "observation {failure_mode, expected_behavior, evidence_summary}, "
    "affected_artifacts (list of exact repo paths under .github/ or adapters/"
    "templates), suggested_change (str), suggested_cases (list of case ids), "
    "success_criteria (list of {metric, operator in [lte,gte,eq,lt,gt], value}), "
    "confidence in [low,medium,high]. Describe aggregate behavior only; never "
    "include secrets, tokens, local paths, or raw logs."
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _load_gateway() -> Callable[[str, str, str], str]:
    """Lazily obtain the LLM gateway from scripts/ (infrastructure edge)."""
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from bench_audit import call_gateway

    return call_gateway


def _parse_draft(raw: str) -> dict:
    """Tolerantly extract the JSON object from an LLM response."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM response contained no JSON object.")
    return json.loads(text[start : end + 1])


def _synthesize_draft(
    run_dir: Path, model: str, gateway: Callable[[str, str, str], str]
) -> dict:
    audit = (run_dir / "audit.md").read_text(encoding="utf-8")[:40000]
    raw = gateway(model, _SYSTEM_PROMPT, audit)
    return _parse_draft(raw)


def _draft_to_proposal(
    draft: dict, *, proposal_id: str, created_at: datetime, source: ProposalSource
) -> Proposal:
    observation = Observation(**draft["observation"])
    validation = ProposalValidation(
        suggested_cases=list(draft.get("suggested_cases", [])),
        success_criteria=[
            SuccessCriterion(**c) for c in draft.get("success_criteria", [])
        ],
    )
    return proposals.build_proposal(
        proposal_id=proposal_id,
        title=draft["title"],
        created_at=created_at,
        source=source,
        observation=observation,
        affected_artifacts=list(draft.get("affected_artifacts", [])),
        suggested_change=SuggestedChange(summary=draft["suggested_change"]),
        validation=validation,
        confidence=draft.get("confidence", "medium"),
    )


def cmd_proposal(args: argparse.Namespace) -> int:
    run_dir = RUNS_DIR / args.run
    if not (run_dir / "audit.md").is_file():
        print(
            f"❌ No audit for '{args.run}'. Run `make bench-audit RUN={args.run}` first."
        )
        return EXIT_PRECONDITION
    evidence = proposals.extract_run_evidence(run_dir)
    try:
        draft = _synthesize_draft(run_dir, args.model, _load_gateway())
    except (ImportError, ValueError, json.JSONDecodeError) as exc:
        print(f"❌ Could not synthesize proposal draft: {exc}")
        return EXIT_MISSING
    created_at = _utc_now()
    source = ProposalSource(repository=LAB_REPOSITORY, runs=[evidence])
    if args.dry_run:
        proposal = _draft_to_proposal(
            draft,
            proposal_id=f"HEP-{created_at.year}-000",
            created_at=created_at,
            source=source,
        )
        print("🧪 DRY_RUN: no files written.\n")
        print(sanitizer.build_public_issue(proposal))
        return EXIT_OK
    proposal_id = proposals.allocate_proposal_id(REPO_ROOT, created_at.year)
    proposal = _draft_to_proposal(
        draft, proposal_id=proposal_id, created_at=created_at, source=source
    )
    preview = sanitizer.build_public_issue(proposal)  # fail early on any leak
    path = proposals.write_proposal(REPO_ROOT, proposal)
    print(f"✅ Wrote proposal {proposal_id}: {path.relative_to(REPO_ROOT)}")
    print(
        f"   Review it, then file the issue with: make harness-proposal-issue PROPOSAL={proposal_id} CREATE=1"
    )
    print("\n--- public issue preview ---\n")
    print(preview)
    return EXIT_OK


def cmd_proposal_issue(args: argparse.Namespace) -> int:
    path = proposals.proposal_path(REPO_ROOT, args.proposal)
    if not path.is_file():
        print(
            f"❌ Proposal {args.proposal} not found at {path.relative_to(REPO_ROOT)}."
        )
        return EXIT_MISSING
    proposal = proposals.load_proposal(path)
    try:
        issue_body = sanitizer.build_public_issue(proposal)
    except sanitizer.SanitizationError as exc:
        print(f"❌ {exc} Fix the proposal before filing.")
        return EXIT_SANITIZATION

    if proposal.links.source_issue:
        print(
            f"✅ Already linked to {proposal.links.source_issue} (idempotent; nothing to do)."
        )
        return EXIT_OK

    if not args.create or args.dry_run:
        print("--- public issue preview ---\n")
        print(issue_body)
        print("\nRe-run with CREATE=1 to file it in the template repo.")
        return EXIT_OK

    if shutil.which("gh") is None:
        print("❌ `gh` not found. Install/authenticate the GitHub CLI to file issues.")
        return EXIT_MISSING
    result = subprocess.run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            TEMPLATE_REPOSITORY,
            "--title",
            f"[HEP] {proposal.title}",
            "--body-file",
            "-",
            "--label",
            "lab-proposal",
            "--label",
            "validation-required",
        ],
        input=issue_body,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"❌ `gh issue create` failed: {result.stderr.strip()}")
        return EXIT_MISSING
    url = result.stdout.strip()
    linked = proposals.link_issue(proposal, url, at=_utc_now(), actor="human")
    proposals.write_proposal(REPO_ROOT, linked)
    print(f"✅ Created and linked issue: {url}")
    return EXIT_OK


def cmd_status(_args: argparse.Namespace) -> int:
    installed = manifests.read_installed_manifest(REPO_ROOT)
    legacy = manifests.read_legacy_template_version(REPO_ROOT)
    print("📦 Harness installed state")
    installed_ref: str | None = None
    if installed is not None:
        info = installed.installed
        installed_ref = info.ref
        print(f"   installed : {info.ref} ({info.commit[:12]}) channel={info.channel}")
        print(f"   protocol  : {installed.sync.protocol}")
    elif legacy is not None:
        installed_ref = str(legacy.get("ref") or "")
        print(f"   installed : {installed_ref} ({legacy.get('commit')}) [legacy]")
        print("   status    : legacy_unverified — adopt via the lifecycle to verify")
    else:
        print("   installed : (none — no .harness-version.yaml or .template-version)")

    if not releases.template_remote_configured(REPO_ROOT):
        print(
            "   latest    : (template remote not configured — run `make template-remote-setup`)"
        )
        return EXIT_OK
    releases.fetch_template(REPO_ROOT)
    latest = releases.latest_release_tag(REPO_ROOT)
    if latest is None:
        print("   latest    : (no v* tags on the template remote)")
        return EXIT_OK
    print(f"   latest    : {latest}")
    if installed_ref and installed_ref != latest:
        print(f"   ⚠️  drift  : installed {installed_ref} ≠ latest {latest}")
    elif installed_ref == latest:
        print("   ✅ up to date")
    latest_protocol = releases.read_sync_protocol_at_ref(REPO_ROOT, latest)
    if (
        installed is not None
        and latest_protocol is not None
        and latest_protocol > installed.sync.protocol
    ):
        print(
            f"   ⚠️  compatibility: {latest} needs sync protocol {latest_protocol}, "
            f"installed {installed.sync.protocol}"
        )
    return EXIT_OK


def cmd_sync_preview(args: argparse.Namespace) -> int:
    try:
        releases.validate_release_ref(args.ref)
    except errors.LifecycleError as exc:
        print(f"❌ {exc}")
        return exc.exit_code
    if not releases.template_remote_configured(REPO_ROOT):
        print("❌ template remote not configured. Run `make template-remote-setup`.")
        return errors.EXIT_MISSING
    releases.fetch_template(REPO_ROOT)
    try:
        commit = releases.resolve_ref_commit(REPO_ROOT, args.ref)
    except errors.LifecycleError as exc:
        print(f"❌ {exc}")
        return exc.exit_code

    gov = sync.governance_paths(REPO_ROOT)
    preview = sync.classify_preview(
        sync.changed_paths_between(REPO_ROOT, "HEAD", args.ref), gov
    )
    print(
        f"🔍 Sync preview for {args.ref} ({commit[:12]}) — read-only, nothing written:"
    )
    print(f"   governance changes: {len(preview.governance)}")
    for path in preview.governance:
        print(f"      · {path}")
    if preview.other:
        print(f"   other (non-governance, ignored): {len(preview.other)}")
    installed = manifests.read_installed_manifest(REPO_ROOT)
    protocol = releases.read_sync_protocol_at_ref(REPO_ROOT, args.ref)
    if (
        protocol is not None
        and installed is not None
        and protocol > installed.sync.protocol
    ):
        print(
            f"   ⚠️  requires sync protocol {protocol} (installed {installed.sync.protocol})."
        )
    if preview.has_blockers:
        print(
            "   ⛔ BLOCKERS — these forbidden paths would change; governance sync must not touch them:"
        )
        for path in preview.forbidden:
            print(f"      · {path}")
        return errors.EXIT_CONFLICT
    print(
        "   ✅ governance-only; safe to prepare a candidate (make harness-sync-branch)."
    )
    return EXIT_OK


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    st = sub.add_parser("status", help="Show installed/latest/drift/compatibility.")
    st.set_defaults(func=cmd_status)

    sp = sub.add_parser("sync-preview", help="Read-only diff of a candidate release.")
    sp.add_argument("--ref", required=True)
    sp.set_defaults(func=cmd_sync_preview)

    pr = sub.add_parser("proposal", help="Draft a versioned proposal from a run.")
    pr.add_argument("--run", required=True)
    pr.add_argument("--model", default=DEFAULT_MODEL)
    pr.add_argument("--dry-run", action="store_true")
    pr.set_defaults(func=cmd_proposal)

    pi = sub.add_parser("proposal-issue", help="Preview or file the public issue.")
    pi.add_argument("--proposal", required=True)
    pi.add_argument("--create", action="store_true")
    pi.add_argument("--dry-run", action="store_true")
    pi.set_defaults(func=cmd_proposal_issue)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return int(args.func(args))
    except errors.LifecycleError as exc:
        print(f"❌ {exc}")
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
