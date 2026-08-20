#!/usr/bin/env python3
"""Selective governance sync from the upstream template.

Pulls ONLY the governance layer (skills, agents, governance docs, adapter
templates) from the template repo at a given tag/branch — not the whole repo —
re-applies this project's package rename to the pulled files, and regenerates the
tool adapters. For full-repo sync use `make template-sync-merge` / `-rebase`.

Run with:
    make template-sync                       # latest tag, all tools
    make template-sync REF=v0.2.0 TOOL=opencode
    make template-sync PREVIEW=1             # show incoming governance diff only
    uv run python scripts/template_sync.py --ref v0.2.0 --tool opencode

Reuses the package-rename rewrite from scripts/init_project.py so pulled files
(e.g. .github/portability.md) keep referencing this project's package, and the
skills_sync engine to regenerate native views/adapters.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from init_project import OLD_DIST, OLD_PACKAGE, rewrite_text

REPO_ROOT = Path(__file__).parent.parent
VERSION_FILE = REPO_ROOT / ".template-version"
REGISTRY = REPO_ROOT / "adapters" / "registry.toml"

# Governance layer pulled by default. Package-agnostic except portability.md /
# agents/README.md, which get the rename re-applied after checkout.
# adapters/registry.toml is intentionally excluded (local tool config + holds the
# optional override of this very list); review it manually if the template changes
# its tool set.
DEFAULT_GOVERNANCE_PATHS: tuple[str, ...] = (
    ".github/skills",
    ".github/skills-external",
    ".github/agents",
    ".github/architecture.md",
    ".github/standards.md",
    ".github/domain-boundaries.md",
    ".github/sdlc.md",
    ".github/orchestration.md",
    ".github/automation.md",
    ".github/portability.md",
    "adapters/templates",
)

KNOWN_TOOLS = ("opencode", "claude", "antigravity", "codex", "copilot")


def run(
    cmd: list[str], check: bool = True, capture: bool = False
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=REPO_ROOT, text=True, check=check, capture_output=capture
    )


def detect_package() -> tuple[str, str]:
    """Return (package_name, dist_name) of THIS project, for rename re-apply."""
    dist = OLD_DIST
    pyproject = REPO_ROOT / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text())
        dist = data.get("project", {}).get("name", OLD_DIST)
    pkg = OLD_PACKAGE
    src = REPO_ROOT / "src"
    if src.is_dir():
        pkgs = [
            p.name for p in src.iterdir() if p.is_dir() and (p / "__init__.py").exists()
        ]
        if pkgs:
            # Prefer the package that ships the skills_sync engine (if multiple exist)
            with_engine = [p for p in pkgs if (src / p / "skills_sync").is_dir()]
            pkg = with_engine[0] if with_engine else pkgs[0]
    return pkg, dist


def governance_paths() -> list[str]:
    """Paths to sync — from registry.toml [template_sync] if present, else default."""
    if REGISTRY.is_file():
        data = tomllib.loads(REGISTRY.read_text())
        paths = data.get("template_sync", {}).get("governance_paths")
        if paths:
            return list(paths)
    return list(DEFAULT_GOVERNANCE_PATHS)


def resolve_ref(remote: str, branch: str, ref: str | None) -> tuple[str, bool]:
    """Return (ref, is_tag). Default: latest v* tag, else <remote>/<branch>.

    When --ref is a bare branch name (no '/' and not a v* tag), qualify it as
    <remote>/<ref> so we read from the template remote, not the local branch.
    """
    if ref:
        is_tag = ref.startswith("v")
        # Bare branch name → qualify with remote so we don't use the local branch
        if not is_tag and "/" not in ref:
            ref = f"{remote}/{ref}"
        return ref, is_tag
    tags = run(
        ["git", "tag", "--list", "v*", "--sort=-v:refname"], capture=True
    ).stdout.split()
    if tags:
        return tags[0], True
    print(f"⚠️  No 'v*' tags found — falling back to {remote}/{branch}.")
    return f"{remote}/{branch}", False


def ref_short_sha(ref: str) -> str:
    return run(["git", "rev-parse", "--short", ref], capture=True).stdout.strip()


def existing_paths_at_ref(ref: str, paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        res = run(["git", "cat-file", "-e", f"{ref}:{p}"], check=False, capture=True)
        if res.returncode == 0:
            out.append(p)
        else:
            print(f"  · {p} — not present at {ref}, skipped")
    return out


def reapply_rename(paths: list[str], pkg: str, dist: str) -> int:
    """Re-apply this project's package rename to the pulled files."""
    if pkg == OLD_PACKAGE and dist == OLD_DIST:
        return 0  # this repo IS the template content; nothing to rewrite
    changed = 0
    for rel in paths:
        target = REPO_ROOT / rel
        files = [target] if target.is_file() else sorted(target.rglob("*"))
        for f in files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if OLD_PACKAGE not in text and OLD_DIST not in text:
                continue
            updated, replaced, _ = rewrite_text(text, pkg, dist)
            if replaced and updated != text:
                f.write_text(updated, encoding="utf-8")
                changed += 1
    return changed


def regenerate(pkg: str, tool: str) -> None:
    module = f"{pkg}.skills_sync"
    if tool == "all":
        run([sys.executable, "-m", module, "sync"])
    else:
        run([sys.executable, "-m", module, "link", "--tool", tool])
        run([sys.executable, "-m", module, "render"])


def write_version_file(ref: str, sha: str, tool: str, paths: list[str]) -> None:
    today = _dt.date.today().isoformat()
    VERSION_FILE.write_text(
        "# Last governance sync from the upstream template (make template-sync).\n"
        f"ref: {ref}\n"
        f"commit: {sha}\n"
        f"date: {today}\n"
        f"tool: {tool}\n"
        "paths:\n" + "".join(f"  - {p}\n" for p in paths)
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--ref", default=None, help="Tag or branch (default: latest v* tag)")
    p.add_argument(
        "--remote", default="template", help="Template git remote (default: template)"
    )
    p.add_argument(
        "--branch", default="main", help="Template branch fallback (default: main)"
    )
    p.add_argument(
        "--tool",
        default="all",
        help=f"Adapter to regenerate: all | {' | '.join(KNOWN_TOOLS)} (default: all)",
    )
    p.add_argument(
        "--preview",
        action="store_true",
        help="Show the incoming governance diff and exit; write nothing",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.tool != "all" and args.tool not in KNOWN_TOOLS:
        sys.exit(f"❌ Unknown TOOL '{args.tool}'. Use: all | {' | '.join(KNOWN_TOOLS)}")

    # Verify the template remote exists.
    if (
        run(
            ["git", "remote", "get-url", args.remote], check=False, capture=True
        ).returncode
        != 0
    ):
        sys.exit(
            f"❌ Git remote '{args.remote}' not found.\n"
            f"   Configure it first: make template-remote-setup"
        )

    print(f"📥 Fetching {args.remote} (with tags)...")
    run(["git", "fetch", args.remote, "--tags"])

    ref, is_tag = resolve_ref(args.remote, args.branch, args.ref)
    paths = existing_paths_at_ref(ref, governance_paths())
    if not paths:
        sys.exit(f"❌ None of the governance paths exist at {ref}. Nothing to sync.")

    print(
        f"🎯 Syncing governance from {'tag' if is_tag else 'ref'} {ref} ({len(paths)} paths)"
    )

    if args.preview:
        print(
            "\n🔍 PREVIEW — incoming governance changes (HEAD → ref), nothing written:\n"
        )
        run(
            ["git", "--no-pager", "diff", "--stat", f"HEAD..{ref}", "--", *paths],
            check=False,
        )
        return 0

    # Pull only the governance paths from the ref into the working tree + index.
    for p in paths:
        run(["git", "checkout", ref, "--", p])
    print(f"  ✓ checked out {len(paths)} governance paths")

    pkg, dist = detect_package()
    rewritten = reapply_rename(paths, pkg, dist)
    if rewritten:
        print(f"  ✓ re-applied package rename ({pkg}) to {rewritten} file(s)")

    print(f"🔧 Regenerating adapters (tool={args.tool})...")
    regenerate(pkg, args.tool)

    sha = ref_short_sha(ref)
    write_version_file(ref, sha, args.tool, paths)

    print("\n✅ Governance sync complete.")
    print(f"   Synced from: {ref} ({sha}) → recorded in .template-version")
    print("   Review the changes:  git diff")
    print("   Verify:              make check-sync && make check")
    print("   Then commit (ideally on a branch).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
