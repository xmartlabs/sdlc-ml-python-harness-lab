"""Discover tool-agnostic agents and project them into native tool formats.

Source of truth: ``.github/agents/*.md`` (frontmatter + system-prompt body).
Projection targets (driven by the registry's ``agent_format``):

- ``claude``   -> ``.claude/agents/<name>.md``    (Claude Code subagent, markdown).
- ``opencode`` -> ``.opencode/agents/<name>.md``  (OpenCode markdown agent + permission).
- ``codex``    -> ``.codex/agents/<name>.toml``   (Codex subagent, TOML).

Agents reference a tier (planner/executor/fast), never a concrete model id, so the
same definition is portable across runtimes. The tier -> model mapping per runtime
is documented in ``.github/portability.md`` and intentionally NOT hardcoded here.
"""

from __future__ import annotations

import os
from pathlib import Path

from ml_python_base.skills_sync.models import (
    AGENT_FORMAT_CLAUDE,
    AGENT_FORMAT_CODEX,
    AGENT_FORMAT_OPENCODE,
    Agent,
    ToolSpec,
)

AGENTS_DIR = Path(".github/agents")
_EXCLUDED_STEMS = frozenset({"README"})

# Map the agnostic tool vocabulary to each runtime's tool names.
_CLAUDE_TOOLS = {
    "read": "Read",
    "grep": "Grep",
    "edit": "Edit",
    "bash": "Bash",
    "task": "Agent",
    "web": "WebFetch",
}

# Tier -> Claude model alias. Aliases (not pinned ids) so each agent always uses
# "whatever opus/sonnet/haiku is current". This makes planning/review run on the
# flagship model while execution runs on Sonnet to save tokens. The same tier
# vocabulary maps to other runtimes in `.github/portability.md`.
# Overridable via ANTHROPIC_MODEL_PLANNER / ANTHROPIC_MODEL_EXECUTOR / ANTHROPIC_MODEL_FAST.
_CLAUDE_TIER_MODEL = {
    "planner": os.getenv("ANTHROPIC_MODEL_PLANNER", "claude-opus-4.8"),
    "executor": os.getenv("ANTHROPIC_MODEL_EXECUTOR", "claude-sonnet-5-oauth"),
    "fast": os.getenv("ANTHROPIC_MODEL_FAST", "claude-fable-5"),
}

# OpenCode governs tool access via a `permission` map (`tools` is deprecated). We
# emit an explicit allow/deny for each controlled key so a read-only agent (e.g.
# planner) genuinely cannot edit or run shell. Agnostic vocab -> OpenCode key.
_OPENCODE_PERM = {
    "read": "read",
    "grep": "grep",
    "edit": "edit",
    "bash": "bash",
    "task": "task",
    "web": "webfetch",
}


# --- Discovery ---------------------------------------------------------------


def discover_agents(root: Path, agents_dir: Path = AGENTS_DIR) -> list[Agent]:
    base = root / agents_dir
    if not base.is_dir():
        return []
    agents: list[Agent] = []
    for path in sorted(base.glob("*.md"), key=lambda p: p.name):
        if path.stem in _EXCLUDED_STEMS:
            continue
        agents.append(_parse_agent(path))
    return agents


def _parse_agent(path: Path) -> Agent:
    front, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    return Agent(
        name=front.get("name", path.stem),
        description=front.get("description", ""),
        kind=front.get("kind", "worker"),
        mode=front.get("mode", "subagent"),
        tier=front.get("tier", "executor"),
        allowed_tools=_as_list(front.get("allowed_tools")),
        governance=_as_list(front.get("governance")),
        skills=_as_list(front.get("skills")),
        delegates_to=_as_list(front.get("delegates_to")),
        context_budget=front.get("context_budget", "medium"),
        body=body.strip(),
    )


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter mapping, body). Minimal YAML subset, no dependency."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    front: dict[str, str] = {}
    end = 1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
        if ":" in lines[i]:
            key, _, value = lines[i].partition(":")
            front[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1 :])
    return front, body


def _as_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    items = [item.strip().strip("\"'") for item in value.split(",")]
    return tuple(item for item in items if item)


# --- Projection --------------------------------------------------------------


def _agent_ext(agent_format: str) -> str:
    """Return the file extension used by each agent format."""
    return ".toml" if agent_format == AGENT_FORMAT_CODEX else ".md"


def render_agent(tool: ToolSpec, agent: Agent) -> str:
    if tool.agent_format == AGENT_FORMAT_CLAUDE:
        return _render_claude(agent)
    if tool.agent_format == AGENT_FORMAT_OPENCODE:
        return _render_opencode(agent)
    if tool.agent_format == AGENT_FORMAT_CODEX:
        return _render_codex(agent)
    raise ValueError(f"Unsupported agent_format: {tool.agent_format!r}")


def _render_claude(agent: Agent) -> str:
    tools = [_CLAUDE_TOOLS[t] for t in agent.allowed_tools if t in _CLAUDE_TOOLS]
    if agent.skills:
        tools.append("Skill")
    front = [
        "---",
        f"name: {agent.name}",
        f"description: {agent.description}",
    ]
    model = _CLAUDE_TIER_MODEL.get(agent.tier)
    if model:
        # Wrap model in quotes to handle version numbers (dots) and hyphens,
        # ensuring the IDE and YAML parsers treat it as a literal string.
        front.append(f'model: "{model}"')
    if tools:
        front.append(f"tools: {', '.join(tools)}")
    front.append("---")
    return "\n".join(front) + "\n\n" + _body_with_footer(agent) + "\n"


def _render_opencode(agent: Agent) -> str:
    front = [
        "---",
        f"description: {agent.description}",
        f"mode: {agent.mode}",
        "permission:",
    ]
    # allow the agent's declared tools, deny every other controlled key.
    allowed = {_OPENCODE_PERM[t] for t in agent.allowed_tools if t in _OPENCODE_PERM}
    for key in sorted(_OPENCODE_PERM.values()):
        front.append(f"  {key}: {'allow' if key in allowed else 'deny'}")
    front.append("---")
    return "\n".join(front) + "\n\n" + _body_with_footer(agent) + "\n"


def _render_codex(agent: Agent) -> str:
    """Render a Codex subagent as TOML (``~/.codex/agents/`` or ``.codex/agents/``).

    The Codex TOML schema keeps the governed information (name, description,
    system prompt) while omitting tool-name mapping — Codex manages its own
    tool permissions separately at the workspace level.
    """
    # TOML multi-line strings use triple-double-quotes.  Escape any that appear
    # in the body to avoid breaking the literal block.
    safe_body = _body_with_footer(agent).replace('"""', "'''")
    lines = [
        f'name = "{agent.name}"',
        f'description = "{agent.description}"',
        f"# tier: {agent.tier}  context_budget: {agent.context_budget}",
        f"# governed by: {', '.join(agent.governance)}",
        "",
        "system_prompt = '''",
        safe_body,
        "'''",
    ]
    return "\n".join(lines) + "\n"


def _body_with_footer(agent: Agent) -> str:
    lines = [agent.body, "", "---", "", "## Governance"]
    refs = ", ".join(f"`.github/{name}.md`" for name in agent.governance)
    lines.append(
        f"Always read and apply: {refs}." if refs else "Apply repository governance."
    )
    if agent.skills:
        lines += ["", "## Bound skills"]
        lines += [
            f"- `{skill}` — read `.github/skills/{skill}.md` before acting."
            for skill in agent.skills
        ]
    lines += [
        "",
        "## Tier",
        f"Intended tier: `{agent.tier}` (context budget: `{agent.context_budget}`). "
        "Runtime model mapping: see `.github/portability.md`.",
    ]
    return "\n".join(lines)


def expected_agent_files(
    root: Path, tool: ToolSpec, agents: list[Agent]
) -> dict[Path, str]:
    """Return ``{absolute_path: content}`` the projection would write."""
    if not tool.has_agent_view:
        return {}
    dest_root = root / tool.native_agents_dir
    ext = _agent_ext(tool.agent_format)
    return {
        dest_root / f"{agent.name}{ext}": render_agent(tool, agent) for agent in agents
    }


def project_agents(root: Path, tool: ToolSpec, agents: list[Agent]) -> int:
    """Write native agent files, removing stale ones. Returns count written."""
    if not tool.has_agent_view:
        return 0
    dest_root = root / tool.native_agents_dir
    dest_root.mkdir(parents=True, exist_ok=True)
    ext = _agent_ext(tool.agent_format)
    desired = {f"{agent.name}{ext}" for agent in agents}

    # Clean up any stale files of the right extension (avoids cross-format leftovers).
    for child in dest_root.iterdir():
        if child.suffix == ext and child.name not in desired:
            child.unlink()

    for path, content in expected_agent_files(root, tool, agents).items():
        path.write_text(content, encoding="utf-8")
    return len(agents)
