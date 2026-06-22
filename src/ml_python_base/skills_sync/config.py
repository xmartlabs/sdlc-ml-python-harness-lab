"""Load and validate the declarative tool registry (``adapters/registry.toml``)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from ml_python_base.skills_sync.errors import RegistryError
from ml_python_base.skills_sync.models import (
    VALID_LINK_STRATEGIES,
    Governance,
    Registry,
    ToolSpec,
)

DEFAULT_REGISTRY_PATH = Path("adapters/registry.toml")


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> Registry:
    """Parse, validate, and return the registry from ``path``."""
    if not path.is_file():
        raise RegistryError(f"Registry not found: {path}")

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - defensive
        raise RegistryError(f"Invalid TOML in {path}: {exc}") from exc

    governance = _parse_governance(raw.get("governance", {}))
    tools = _parse_tools(raw.get("tool", []))
    if not tools:
        raise RegistryError("Registry declares no [[tool]] entries.")

    return Registry(
        schema_version=int(raw.get("schema_version", 1)),
        governance=governance,
        tools=tuple(tools),
    )


def _parse_governance(block: dict) -> Governance:
    files = tuple(block.get("files", ()))
    return Governance(
        files=files,
        automation=str(block.get("automation", "")),
        orchestration=str(block.get("orchestration", "")),
    )


def _parse_tools(entries: list[dict]) -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    seen: set[str] = set()
    for entry in entries:
        tool = _parse_tool(entry)
        if tool.id in seen:
            raise RegistryError(f"Duplicate tool id in registry: {tool.id}")
        seen.add(tool.id)
        tools.append(tool)
    return tools


def _parse_tool(entry: dict) -> ToolSpec:
    tool_id = entry.get("id")
    if not tool_id:
        raise RegistryError(f"A [[tool]] entry is missing 'id': {entry!r}")

    strategy = entry.get("link_strategy", "none")
    if strategy not in VALID_LINK_STRATEGIES:
        raise RegistryError(
            f"Tool '{tool_id}' has invalid link_strategy '{strategy}'. "
            f"Expected one of {sorted(VALID_LINK_STRATEGIES)}."
        )

    needs_manifest = bool(entry.get("needs_manifest", False))
    manifest_path = entry.get("manifest_path")
    if needs_manifest and not manifest_path:
        raise RegistryError(
            f"Tool '{tool_id}' sets needs_manifest but no manifest_path."
        )

    return ToolSpec(
        id=str(tool_id),
        display_name=str(entry.get("display_name", tool_id)),
        link_strategy=strategy,
        native_skills_dir=str(entry.get("native_skills_dir", "")),
        needs_manifest=needs_manifest,
        manifest_path=manifest_path,
        adapter_file=entry.get("adapter_file"),
        adapter_template=entry.get("adapter_template"),
        reference_prefix=str(entry.get("reference_prefix", "")),
        adapter_frontmatter=entry.get("adapter_frontmatter"),
        native_agents_dir=str(entry.get("native_agents_dir", "")),
        agent_format=str(entry.get("agent_format", "")),
    )
