"""Render the machine-managed skill region inside each tool's adapter file.

Only the text between the sentinels below is owned by the engine; the
surrounding governance prose stays hand-written. This guarantees the skill
lists across CLAUDE.md / AGENTS.md / OPENCODE.md / GEMINI.md / copilot can never
drift from the actual governed skills.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from ml_python_base.skills_sync.discovery import discover_skills
from ml_python_base.skills_sync.errors import SkillsSyncError
from ml_python_base.skills_sync.models import KIND_INTERNAL, Registry, Skill, ToolSpec

BEGIN_MARKER = "<!-- BEGIN GENERATED SKILLS (managed by skills_sync; do not edit) -->"
END_MARKER = "<!-- END GENERATED SKILLS -->"
TEMPLATES_DIR = Path("adapters/templates")
DEFAULT_TEMPLATE = "_skills_block.j2"
REFRESH_COMMAND = "make sync-skills"


def _environment(root: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(root / TEMPLATES_DIR)),
        # Templates render Markdown for local adapter files (no HTML/XSS surface);
        # select_autoescape leaves .j2 Markdown untouched while satisfying linters.
        autoescape=select_autoescape(default=False),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def render_region(
    root: Path,
    registry: Registry,
    tool: ToolSpec,
    skills: list[Skill] | None = None,
) -> str:
    """Render the managed region content (no surrounding markers)."""
    skills = skills if skills is not None else discover_skills(root)
    env = _environment(root)
    template_name = tool.adapter_template or DEFAULT_TEMPLATE
    template = env.get_template(template_name)
    context = {
        "tool": tool,
        "ref": tool.reference_prefix,
        "internal_skills": [s for s in skills if s.kind == KIND_INTERNAL],
        "external_skills": [s for s in skills if s.kind != KIND_INTERNAL],
        "governance": registry.governance,
        "refresh_command": REFRESH_COMMAND,
    }
    return template.render(**context).strip()


def splice(existing: str, region: str) -> str:
    """Replace the content between the sentinels with ``region``."""
    if BEGIN_MARKER not in existing or END_MARKER not in existing:
        raise SkillsSyncError(
            "Adapter file is missing the generated-skills sentinels. Add:\n"
            f"  {BEGIN_MARKER}\n  {END_MARKER}\n"
            "around the skills section so the engine can manage it."
        )
    begin_at = existing.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    end_at = existing.index(END_MARKER)
    return f"{existing[:begin_at]}\n{region}\n{existing[end_at:]}"


def expected_file(root: Path, registry: Registry, tool: ToolSpec) -> str | None:
    """Return what the adapter file should contain, or None if no adapter."""
    if not tool.adapter_file:
        return None
    path = root / tool.adapter_file
    if not path.is_file():
        return None
    region = render_region(root, registry, tool)
    return splice(path.read_text(encoding="utf-8"), region)


def render_tool(root: Path, registry: Registry, tool: ToolSpec) -> bool:
    """Write the managed region into the adapter file. Returns True if changed."""
    if not tool.adapter_file:
        return False
    path = root / tool.adapter_file
    if not path.is_file():
        return False
    current = path.read_text(encoding="utf-8")
    updated = splice(current, render_region(root, registry, tool))
    if updated == current:
        return False
    path.write_text(updated, encoding="utf-8")
    return True
