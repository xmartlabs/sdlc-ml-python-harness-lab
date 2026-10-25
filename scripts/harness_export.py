#!/usr/bin/env python3
"""Carry the lab's harness — or just one skill — into your own repository.

    make harness-export REPO=../my-project                     # preview, writes nothing
    make harness-export REPO=../my-project APPLY=1             # write it
    make harness-export REPO=../my-project SKILLS=systematic_debugging GOVERNANCE=0 APPLY=1

With GOVERNANCE=0 only the named skills are copied into the tool's native skill
folder; with governance (the default) the rules file, agents and governance
documents come too.

It is the same projection the `injected` experiment arm uses (ADR-0031), aimed at
a repository you own instead of a run's workspace (ADR-0072). A preview projects
into a throwaway copy and reports what would change; nothing touches the
repository until APPLY=1. Files it displaces are kept as `*.native.bak`, and it
refuses a repository with uncommitted work, so the result is reviewed and
reverted with git like any other change.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class ExportError(Exception):
    """A request the export refuses, with the reason a person can act on."""


@dataclass
class ExportPlan:
    tool: str
    skills: list[str] = field(default_factory=list)
    agents: int = 0
    files_written: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)
    applied: bool = False


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _check_target(repo: Path, *, apply: bool) -> Path:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise ExportError(f"{repo} is not a directory.")
    if repo == REPO_ROOT or REPO_ROOT in repo.parents:
        raise ExportError(
            "That is the lab itself. Point REPO at the repository you want the "
            "harness in."
        )
    if _git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        raise ExportError(
            f"{repo} is not a git repository. The export is reviewed and reverted "
            "with git, so it only writes into one."
        )
    if apply and _git(repo, "status", "--porcelain").stdout.strip():
        raise ExportError(
            f"{repo} has uncommitted changes. Commit or stash them first, so the "
            "export shows up as its own diff you can review or revert."
        )
    return repo


def _resolve_skills(names: list[str] | None) -> list:
    from ml_python_base.skills_sync.discovery import discover_skills

    available = discover_skills(REPO_ROOT)
    if not names:
        return available
    by_name = {skill.name: skill for skill in available}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise ExportError(
            f"Unknown skill(s): {', '.join(unknown)}. Known: "
            + ", ".join(sorted(by_name))
        )
    return [by_name[name] for name in names]


def _files(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` but git's own, with its bytes."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _project(target: Path, tool: str, skills: list, governance: bool) -> ExportPlan:
    from ml_python_base.skills_sync.config import DEFAULT_REGISTRY_PATH, load_registry
    from ml_python_base.skills_sync.projection import project_harness

    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)
    if tool not in {t.id for t in registry.tools}:
        raise ExportError(
            f"Unknown tool {tool!r}. Known: "
            + ", ".join(sorted(t.id for t in registry.tools))
        )
    docs: tuple[str, ...] = ()
    if governance:
        docs = tuple(
            doc
            for doc in (
                *registry.governance.files,
                registry.governance.automation,
                registry.governance.orchestration,
            )
            if doc
        )
    before = _files(target)
    if governance:
        result = project_harness(
            REPO_ROOT,
            target,
            registry,
            skills=skills,
            tool_ids=(tool,),
            governance_files=docs,
        )
        names, agents, collisions = result.skills, result.agents, result.collisions
    else:
        # Skills only: copied into the tool's native skill folder and nothing
        # else — no agents, no rules file of the lab's in your repository.
        from ml_python_base.skills_sync.linker import link_tool
        from ml_python_base.skills_sync.models import LINK_COPY

        spec = next(t for t in registry.tools if t.id == tool)
        if not spec.native_skills_dir:
            raise ExportError(
                f"{tool} reads no native skill folder, so a skills-only export "
                "would write nothing. Keep the governance (drop GOVERNANCE=0) or "
                "pick a tool with one: claude, opencode, codex, antigravity."
            )
        link_tool(target, spec, skills, strategy=LINK_COPY)
        names, agents, collisions = [skill.name for skill in skills], 0, []
    after = _files(target)
    # The projection reports governance and adapter files, not the skills and
    # agents it copies; what changed on disk is the complete answer.
    changed = sorted(
        rel
        for rel, body in after.items()
        if before.get(rel) != body and not rel.endswith(".native.bak")
    )
    return ExportPlan(
        tool=tool,
        skills=sorted(names),
        agents=agents,
        files_written=changed,
        collisions=sorted(set(collisions)),
    )


def export(
    repo: Path,
    *,
    tool: str = "claude",
    skills: list[str] | None = None,
    governance: bool = True,
    apply: bool = False,
) -> ExportPlan:
    """Project the harness into ``repo``, or preview what that would write."""
    target = _check_target(repo, apply=apply)
    chosen = _resolve_skills(skills)
    if apply:
        plan = _project(target, tool, chosen, governance)
        plan.applied = True
        return plan
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / target.name
        shutil.copytree(
            target,
            copy,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "node_modules", ".venv", "venv"),
        )
        return _project(copy, tool, chosen, governance)


def _print(plan: ExportPlan, repo: Path) -> None:
    verb = "Wrote" if plan.applied else "Would write"
    print(f"\n{verb} into {repo} for {plan.tool}:")
    print(f"  skills ({len(plan.skills)}): {', '.join(plan.skills) or '—'}")
    print(f"  agents: {plan.agents}")
    print(f"  files: {len(plan.files_written)}")
    for rel in plan.files_written[:40]:
        print(f"    {rel}")
    if len(plan.files_written) > 40:
        print(f"    … and {len(plan.files_written) - 40} more")
    if plan.collisions:
        kept = "kept" if plan.applied else "would be kept"
        print(f"  replaces your own ({kept} as *.native.bak):")
        for rel in plan.collisions:
            print(f"    {rel}")
    if plan.applied:
        print(f"\nReview with: git -C {repo} status && git -C {repo} diff")
        print(f"Undo with:   git -C {repo} checkout -- . && git -C {repo} clean -fd")
    else:
        print("\nNothing was written. Add APPLY=1 to write it.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path)
    parser.add_argument(
        "--list", action="store_true", help="print the skill names you can export"
    )
    parser.add_argument("--tool", default="claude")
    parser.add_argument(
        "--skills", default="", help="comma-separated skill names; empty = every skill"
    )
    parser.add_argument(
        "--no-governance",
        action="store_true",
        help="skills only — no governance documents, agents or rules file",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        from ml_python_base.skills_sync.discovery import discover_skills

        for skill in discover_skills(REPO_ROOT):
            print(f"{skill.name}  ({skill.kind})")
        return 0
    if args.repo is None:
        parser.error("--repo is required (or --list to see the skills)")
    names = [name.strip() for name in args.skills.split(",") if name.strip()]
    try:
        plan = export(
            args.repo,
            tool=args.tool,
            skills=names or None,
            governance=not args.no_governance,
            apply=args.apply,
        )
    except ExportError as error:
        print(f"✗ {error}", file=sys.stderr)
        return 1
    _print(plan, args.repo.expanduser().resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
