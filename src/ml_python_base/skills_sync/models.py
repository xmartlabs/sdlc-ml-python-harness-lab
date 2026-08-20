"""Immutable data models for the skills sync engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Link strategies a tool can use to materialize its native skills view.
LINK_SYMLINK = "symlink"
LINK_COPY = "copy"
LINK_NONE = "none"
VALID_LINK_STRATEGIES = frozenset({LINK_SYMLINK, LINK_COPY, LINK_NONE})

# Skill provenance.
KIND_INTERNAL = "internal"
KIND_EXTERNAL = "external"

# Agent kinds and modes.
AGENT_ORCHESTRATOR = "orchestrator"
AGENT_WORKER = "worker"
MODE_PRIMARY = "primary"
MODE_SUBAGENT = "subagent"

# Native agent projection formats.
AGENT_FORMAT_CLAUDE = "claude"
AGENT_FORMAT_OPENCODE = "opencode"
AGENT_FORMAT_CODEX = "codex"  # TOML, lives at .codex/agents/<name>.toml


@dataclass(frozen=True)
class Skill:
    """A governed skill discovered from the source of truth.

    Internal skills are flat files (``.github/skills/<name>.md``); external
    skills are directories (``.github/skills-external/<name>/`` with a
    ``SKILL.md`` plus optional supporting files).
    """

    name: str
    kind: str  # KIND_INTERNAL | KIND_EXTERNAL
    source_path: Path  # file (internal) or directory (external)
    description: str = ""

    @property
    def is_internal(self) -> bool:
        return self.kind == KIND_INTERNAL


@dataclass(frozen=True)
class ToolSpec:
    """One AI tool that consumes governed skills, declared in the registry."""

    id: str
    display_name: str
    link_strategy: str
    native_skills_dir: str = ""
    needs_manifest: bool = False
    manifest_path: str | None = None
    adapter_file: str | None = None
    adapter_template: str | None = None
    reference_prefix: str = ""
    adapter_frontmatter: str | None = None
    native_agents_dir: str = ""
    agent_format: str = ""

    @property
    def has_native_view(self) -> bool:
        return bool(self.native_skills_dir) and self.link_strategy != LINK_NONE

    @property
    def has_agent_view(self) -> bool:
        return bool(self.native_agents_dir) and bool(self.agent_format)


@dataclass(frozen=True)
class Agent:
    """A governed, tool-agnostic agent definition from ``.github/agents``."""

    name: str
    description: str
    kind: str  # AGENT_ORCHESTRATOR | AGENT_WORKER
    mode: str  # MODE_PRIMARY | MODE_SUBAGENT
    tier: str  # planner | executor | fast
    allowed_tools: tuple[str, ...]
    governance: tuple[str, ...]
    skills: tuple[str, ...]
    delegates_to: tuple[str, ...]
    context_budget: str
    body: str

    @property
    def is_orchestrator(self) -> bool:
        return self.kind == AGENT_ORCHESTRATOR


@dataclass(frozen=True)
class Governance:
    """Governance files surfaced in every generated adapter region."""

    files: tuple[str, ...]
    automation: str
    orchestration: str


@dataclass(frozen=True)
class Registry:
    """The parsed declarative registry."""

    schema_version: int
    governance: Governance
    tools: tuple[ToolSpec, ...]

    def tool(self, tool_id: str) -> ToolSpec:
        for spec in self.tools:
            if spec.id == tool_id:
                return spec
        raise KeyError(tool_id)
