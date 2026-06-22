"""Enumerate governed skills and apply the internal-over-external precedence."""

from __future__ import annotations

from pathlib import Path

from ml_python_base.skills_sync.models import KIND_EXTERNAL, KIND_INTERNAL, Skill

INTERNAL_DIR = Path(".github/skills")
EXTERNAL_DIR = Path(".github/skills-external")
SKILL_FILE = "SKILL.md"
_EXCLUDED_STEMS = frozenset({"README"})


def discover_skills(
    root: Path,
    internal_dir: Path = INTERNAL_DIR,
    external_dir: Path = EXTERNAL_DIR,
) -> list[Skill]:
    """Return internal skills (sorted) followed by external skills (sorted).

    The ordering matches the legacy shell glob expansion, which is what keeps
    the generated manifest byte-identical. External skills whose name collides
    with an internal skill are dropped (internal precedence).
    """
    internal = _discover_internal(root / internal_dir)
    internal_names = {skill.name for skill in internal}
    external = _discover_external(root / external_dir, internal_names)
    return internal + external


def _discover_internal(internal_path: Path) -> list[Skill]:
    if not internal_path.is_dir():
        return []
    skills: list[Skill] = []
    for path in sorted(internal_path.glob("*.md"), key=lambda p: p.name):
        if path.stem in _EXCLUDED_STEMS:
            continue
        skills.append(
            Skill(
                name=path.stem,
                kind=KIND_INTERNAL,
                source_path=path,
                description=_read_description(path),
            )
        )
    return skills


def _discover_external(external_path: Path, internal_names: set[str]) -> list[Skill]:
    if not external_path.is_dir():
        return []
    skills: list[Skill] = []
    for directory in sorted(external_path.iterdir(), key=lambda p: p.name):
        if not directory.is_dir():
            continue
        if directory.name in internal_names:
            continue  # internal precedence
        skill_file = directory / SKILL_FILE
        if not skill_file.is_file():
            continue
        skills.append(
            Skill(
                name=directory.name,
                kind=KIND_EXTERNAL,
                source_path=directory,
                description=_read_description(skill_file),
            )
        )
    return skills


def _read_description(skill_file: Path) -> str:
    """Extract the ``description:`` value from the YAML frontmatter, if present.

    Kept dependency-free (no YAML parser): we only need the single-line
    description used to render adapter skill lists.
    """
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    lines = text.splitlines()
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            value = line.split(":", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return ""
