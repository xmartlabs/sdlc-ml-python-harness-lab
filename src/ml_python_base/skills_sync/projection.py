"""Project a governed harness into a foreign repository.

The engine's normal job is to keep *this* repository's agent-visible surfaces in
sync with its governed sources. Projection does the same work across a boundary:
the skills, agents, adapter files and governance documents come from a
``source_root`` and are materialized into a ``target_root`` the source does not
own — a cloned target repository, for one run.

Two properties make this different from an in-place sync, and both are the
reason this is a separate module rather than a flag:

**Symlinks cannot cross the boundary.** ``link_tool``'s symlink strategy encodes
a path relative to the root it writes into. A skill living in the lab is not
reachable by a relative path from a cloned repository, so projection always
copies — the target must be self-contained, because it is archived and read
after the lab has moved on.

**The target may already have governance of its own.** Overwriting a repository's
``CLAUDE.md`` is a real change to the thing being measured, so every collision is
backed up and reported rather than applied silently.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ml_python_base.skills_sync.agents import discover_agents, project_agents
from ml_python_base.skills_sync.discovery import discover_skills
from ml_python_base.skills_sync.linker import link_tool
from ml_python_base.skills_sync.models import LINK_COPY
from ml_python_base.skills_sync.renderer import render_tool

if TYPE_CHECKING:
    from pathlib import Path

    from ml_python_base.skills_sync.config import Registry
    from ml_python_base.skills_sync.models import Skill

# Suffix given to a file the projection displaced. Kept inside the target so the
# run's own artifacts show exactly what the harness replaced.
BACKUP_SUFFIX = ".native.bak"


@dataclass
class ProjectionResult:
    """What a projection actually did, for the run's condition manifest."""

    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    agents: int = 0
    files_written: list[str] = field(default_factory=list)
    # Paths that already existed in the target and were displaced. This is the
    # field that must reach the condition: a harness projected over a repo's own
    # governance is not the same experiment as one projected onto a blank slate.
    collisions: list[str] = field(default_factory=list)


def _place(source: Path, target: Path, result: ProjectionResult, rel: str) -> None:
    """Copy ``source`` over ``target``, backing up whatever was there."""
    if not source.exists():
        return
    if target.exists() or target.is_symlink():
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        if backup.exists():
            backup.unlink()
        target.replace(backup)
        result.collisions.append(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
    result.files_written.append(rel)


def project_harness(
    source_root: Path,
    target_root: Path,
    registry: Registry,
    *,
    skills: list[Skill] | None = None,
    tool_ids: tuple[str, ...] | None = None,
    governance_files: tuple[str, ...] = (),
) -> ProjectionResult:
    """Materialize ``source_root``'s harness inside ``target_root``.

    ``skills`` overrides discovery, so a projected harness can carry the same
    per-run ablation an in-place run does — otherwise an ablated arm would be
    ablated in the lab and complete in the target.

    ``tool_ids`` narrows the projection to the tools a run actually uses.
    Projecting every registered tool would litter the target with surfaces no
    agent reads, and those files are visible to the agent.
    """
    result = ProjectionResult()
    resolved = skills if skills is not None else discover_skills(source_root)
    result.skills = [skill.name for skill in resolved]
    agents = discover_agents(source_root)

    for doc in governance_files:
        _place(source_root / doc, target_root / doc, result, doc)

    for tool in registry.tools:
        if tool_ids is not None and tool.id not in tool_ids:
            continue
        result.tools.append(tool.id)

        # Copy, never symlink: the target has to stand on its own once the run
        # is archived and the lab is out of reach.
        link_tool(target_root, tool, resolved, strategy=LINK_COPY)

        if tool.native_agents_dir and tool.agent_format:
            result.agents = project_agents(target_root, tool, agents)

        if not tool.adapter_file:
            continue
        # The adapter file must exist (with sentinels) before its managed region
        # can be spliced, and a foreign repository has no reason to have one —
        # so the source's file is placed first and then re-rendered against the
        # effective skill set.
        _place(
            source_root / tool.adapter_file,
            target_root / tool.adapter_file,
            result,
            tool.adapter_file,
        )
        render_tool(
            target_root,
            registry,
            tool,
            skills=resolved,
            template_root=source_root,
        )

    return result
