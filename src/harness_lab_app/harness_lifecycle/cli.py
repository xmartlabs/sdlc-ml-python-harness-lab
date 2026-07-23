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
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from harness_lab_app.harness_lifecycle import (
    drafting,
    errors,
    locking,
    manifests,
    proposals,
    releases,
    sanitizer,
    sync,
)
from harness_lab_app.harness_lifecycle.models import (
    CandidateManifest,
    ProposalSource,
)

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


def _utc_now() -> datetime:
    return datetime.now(UTC)


def cmd_proposal(args: argparse.Namespace) -> int:
    run_dir = RUNS_DIR / args.run
    if not (run_dir / "audit.md").is_file():
        print(
            f"❌ No audit for '{args.run}'. Run `make bench-audit RUN={args.run}` first."
        )
        return EXIT_PRECONDITION
    evidence = proposals.extract_run_evidence(run_dir)
    try:
        draft = drafting.synthesize_draft(run_dir, args.model, drafting.load_gateway())
    except (ImportError, ValueError, json.JSONDecodeError) as exc:
        print(f"❌ Could not synthesize proposal draft: {exc}")
        return EXIT_MISSING
    created_at = _utc_now()
    source = ProposalSource(repository=LAB_REPOSITORY, runs=[evidence])
    if args.dry_run:
        proposal = drafting.draft_to_proposal(
            draft,
            proposal_id=f"HEP-{created_at.year}-000",
            created_at=created_at,
            source=source,
        )
        print("🧪 DRY_RUN: no files written.\n")
        print(sanitizer.build_public_issue(proposal))
        return EXIT_OK
    proposal_id = proposals.allocate_proposal_id(REPO_ROOT, created_at.year)
    proposal = drafting.draft_to_proposal(
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


def _protocol_incompatibility(ref: str) -> tuple[int, int] | None:
    """Return (current, candidate) if adopting ``ref`` needs a newer sync protocol.

    A protocol bump lives in ``adapters/registry.toml`` (a platform path), so this
    is the signal that a governance-only sync is not enough — the platform/registry
    upgrade must be adopted first. Baseline is the installed manifest, else the
    repo's own current registry protocol.
    """
    installed = manifests.read_installed_manifest(REPO_ROOT)
    current = (
        installed.sync.protocol
        if installed is not None
        else (sync.read_registry_protocol(REPO_ROOT) or 0)
    )
    candidate = releases.read_sync_protocol_at_ref(REPO_ROOT, ref) or 0
    return (current, candidate) if candidate > current else None


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

    governance = sync.governance_paths(REPO_ROOT)
    gov_changes = sync.changed_paths_between(REPO_ROOT, "HEAD", args.ref, governance)
    print(
        f"🔍 Sync preview for {args.ref} ({commit[:12]}) — read-only, nothing written:"
    )
    print(f"   governance changes: {len(gov_changes)}")
    for _status, path in gov_changes:
        print(f"      · {path}")
    incompatible = _protocol_incompatibility(args.ref)
    if incompatible is not None:
        current, candidate = incompatible
        print(
            f"   ⛔ sync protocol {candidate} required, but this repo is at {current}. "
            "Adopt the platform (registry) upgrade before a governance-only sync."
        )
        return errors.EXIT_INCOMPATIBLE
    print(
        "   ✅ governance-only and protocol-compatible; safe to prepare a candidate "
        "(make harness-sync-branch)."
    )
    return EXIT_OK


def _regenerate_adapters(worktree: Path) -> bool:
    """Regenerate native adapter/skill views inside the candidate worktree."""
    result = subprocess.run(
        ["uv", "run", "python", "-m", "ml_python_base.skills_sync", "sync"],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def cmd_sync_branch(args: argparse.Namespace) -> int:
    try:
        releases.validate_release_ref(args.ref)
    except errors.LifecycleError as exc:
        print(f"❌ {exc}")
        return exc.exit_code
    if not releases.template_remote_configured(REPO_ROOT):
        print("❌ template remote not configured. Run `make template-remote-setup`.")
        return errors.EXIT_MISSING
    if not sync.main_tree_clean(REPO_ROOT):
        print(
            "❌ Main working tree is not clean. Commit or stash before preparing a candidate."
        )
        return errors.EXIT_PRECONDITION
    releases.fetch_template(REPO_ROOT)
    try:
        commit = releases.resolve_ref_commit(REPO_ROOT, args.ref)
    except errors.LifecycleError as exc:
        print(f"❌ {exc}")
        return exc.exit_code

    governance = sync.governance_paths(REPO_ROOT)
    incompatible = _protocol_incompatibility(args.ref)
    if incompatible is not None:
        current, candidate = incompatible
        print(
            f"❌ {args.ref} needs sync protocol {candidate} but this repo is at "
            f"{current}. Adopt the platform (registry) upgrade first."
        )
        return errors.EXIT_INCOMPATIBLE

    worktree_path = sync.candidate_worktree_path(REPO_ROOT, args.ref)
    if worktree_path.exists():
        print(
            f"❌ Candidate worktree already exists: {worktree_path.relative_to(REPO_ROOT)}. "
            f"Remove it with `make harness-sync-clean REF={args.ref}`."
        )
        return errors.EXIT_PRECONDITION

    try:
        with locking.file_lock(REPO_ROOT / sync.WORKTREES_DIR / ".lock"):
            worktree = sync.create_candidate_worktree(REPO_ROOT, args.ref)
            try:
                applied = sync.apply_governance_sync(worktree, args.ref, governance)
                touched_forbidden = sync.forbidden_changes(
                    sync.worktree_changed_paths(worktree)
                )
                if touched_forbidden:
                    raise errors.LifecycleError(
                        "Governance sync unexpectedly changed forbidden paths: "
                        + ", ".join(touched_forbidden),
                        errors.EXIT_CONFLICT,
                    )
                regenerated = _regenerate_adapters(worktree)
                manifests.write_candidate_manifest(
                    worktree,
                    CandidateManifest(
                        repository=TEMPLATE_REPOSITORY,
                        ref=args.ref,
                        commit=commit,
                        branch=sync.candidate_branch(args.ref),
                        worktree=str(worktree),
                        created_at=_utc_now(),
                        governance_paths=applied,
                    ),
                )
            except BaseException:
                sync.remove_candidate_worktree(REPO_ROOT, args.ref)
                raise
    except errors.LifecycleError as exc:
        print(f"❌ {exc}")
        return exc.exit_code

    rel = worktree.relative_to(REPO_ROOT)
    print(f"✅ Candidate prepared in an isolated worktree (main untouched): {rel}")
    print(
        f"   ref {args.ref} ({commit[:12]})  branch {sync.candidate_branch(args.ref)}"
    )
    print(f"   governance paths applied: {len(applied)}")
    if not regenerated:
        print(
            "   ⚠️  adapter regeneration failed — run `make sync-skills` inside the worktree."
        )
    print(f"   Review:   git -C {rel} diff HEAD")
    print(f"   Clean up: make harness-sync-clean REF={args.ref}")
    return EXIT_OK


def cmd_sync_clean(args: argparse.Namespace) -> int:
    try:
        releases.validate_release_ref(args.ref)
    except errors.LifecycleError as exc:
        print(f"❌ {exc}")
        return exc.exit_code
    if not sync.candidate_worktree_path(REPO_ROOT, args.ref).exists():
        print(f"✅ No candidate worktree for {args.ref} (nothing to clean).")
        return EXIT_OK
    sync.remove_candidate_worktree(REPO_ROOT, args.ref)
    print(f"✅ Removed the candidate worktree and branch for {args.ref}.")
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

    sb = sub.add_parser(
        "sync-branch", help="Prepare a candidate in an isolated worktree."
    )
    sb.add_argument("--ref", required=True)
    sb.set_defaults(func=cmd_sync_branch)

    sc = sub.add_parser("sync-clean", help="Remove a candidate worktree and branch.")
    sc.add_argument("--ref", required=True)
    sc.set_defaults(func=cmd_sync_clean)

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
