"""Skill profiles: named subsets of the governed catalog (ADR-0085).

Every listed skill costs context on every run — its description sits in each
adapter file whether or not the agent ever opens it — and the lab's census
showed most skills are never opened. A profile names the subset a project (or a
model tier) actually carries.

Two files, two owners:

* ``.github/skill-profiles.toml`` — the **definitions**. Governed, so it
  travels with template syncs alongside the skills it names.
* ``harness.toml`` at the repository root — the **selection**: which profile
  this project uses, plus per-project additions and removals. Owned by the
  project; no sync ever writes it. Absent means the full catalog, which is what
  every repository did before profiles existed.

A profile **filters, it never deletes**: the source directories stay complete,
so a later template sync cannot resurrect a skill that a bootstrap had removed,
and re-enabling one is a one-line change. Only the generated views (native skill
dirs, adapter listings) follow the profile.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ml_python_base.skills_sync.errors import SkillsSyncError

if TYPE_CHECKING:
    from ml_python_base.skills_sync.models import Skill

PROFILES_FILE = Path(".github/skill-profiles.toml")
SELECTION_FILE = Path("harness.toml")
# The profile that means "no filter". Always defined, never needs declaring.
FULL = "full"


class ProfileError(SkillsSyncError):
    """A profile definition or selection that cannot be applied."""


@dataclass(frozen=True)
class Profile:
    """A named skill subset. ``skills`` is None for the full catalog."""

    name: str
    description: str = ""
    skills: frozenset[str] | None = None
    extends: str = ""


@dataclass(frozen=True)
class Selection:
    """What a project carries: a profile, then its own additions and removals."""

    profile: str = FULL
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    source: str = ""  # the file it came from; "" for the implicit default

    @property
    def is_default(self) -> bool:
        return self.profile == FULL and not self.include and not self.exclude


@dataclass(frozen=True)
class Resolution:
    """The effective skill set, and what the selection named that does not exist."""

    skills: list[Skill]
    dropped: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"Invalid TOML in {path}: {exc}") from exc


def load_profiles(root: Path) -> dict[str, Profile]:
    """Every defined profile, with ``extends`` resolved. ``full`` always exists."""
    profiles: dict[str, Profile] = {
        FULL: Profile(name=FULL, description="Every governed skill.")
    }
    path = root / PROFILES_FILE
    if not path.is_file():
        return profiles
    raw = _read_toml(path).get("profile", {})
    if not isinstance(raw, dict):
        raise ProfileError(f"{PROFILES_FILE}: expected [profile.<name>] tables")
    declared: dict[str, dict] = {}
    for name, body in raw.items():
        if name == FULL:
            raise ProfileError(
                f"{PROFILES_FILE}: '{FULL}' is built in; do not declare it"
            )
        if not isinstance(body, dict):
            raise ProfileError(f"{PROFILES_FILE}: profile '{name}' must be a table")
        declared[name] = body

    def resolve(name: str, chain: tuple[str, ...]) -> frozenset[str]:
        if name in chain:
            raise ProfileError(
                f"{PROFILES_FILE}: profile cycle {' -> '.join((*chain, name))}"
            )
        body = declared[name]
        own = frozenset(str(s) for s in body.get("skills", []))
        parent = str(body.get("extends", ""))
        if not parent:
            return own
        if parent == FULL:
            raise ProfileError(
                f"{PROFILES_FILE}: '{name}' extends '{FULL}', which is not a subset; "
                "use an exclude list in harness.toml instead"
            )
        if parent not in declared:
            raise ProfileError(f"{PROFILES_FILE}: '{name}' extends unknown '{parent}'")
        return own | resolve(parent, (*chain, name))

    for name, body in declared.items():
        profiles[name] = Profile(
            name=name,
            description=str(body.get("description", "")),
            skills=resolve(name, ()),
            extends=str(body.get("extends", "")),
        )
    return profiles


def load_selection(root: Path) -> Selection:
    """The project's selection from ``harness.toml``, or the full catalog."""
    path = root / SELECTION_FILE
    if not path.is_file():
        return Selection()
    block = _read_toml(path).get("skills", {})
    if not isinstance(block, dict):
        raise ProfileError(f"{SELECTION_FILE}: expected a [skills] table")
    return Selection(
        profile=str(block.get("profile", FULL)) or FULL,
        include=tuple(sorted({str(s) for s in block.get("include", [])})),
        exclude=tuple(sorted({str(s) for s in block.get("exclude", [])})),
        source=str(SELECTION_FILE),
    )


def wanted_names(
    catalog: list[str], profiles: dict[str, Profile], selection: Selection
) -> set[str]:
    """The names a selection keeps from ``catalog`` (unknown names ignored)."""
    if selection.profile not in profiles:
        known = ", ".join(sorted(profiles))
        raise ProfileError(
            f"Unknown skill profile '{selection.profile}' (from "
            f"{selection.source or 'default'}). Defined: {known}."
        )
    profile = profiles[selection.profile]
    base = set(catalog) if profile.skills is None else set(profile.skills)
    return (base | set(selection.include)) - set(selection.exclude)


def resolve_skills(
    skills: list[Skill],
    profiles: dict[str, Profile],
    selection: Selection,
) -> Resolution:
    """Filter discovered ``skills`` by ``selection``, keeping discovery order.

    Names the profile or selection mention that are not in the catalog are
    reported in ``unknown`` rather than raised: a template sync may rename or
    retire a skill, and a project's harness must still build.
    """
    catalog = [skill.name for skill in skills]
    wanted = wanted_names(catalog, profiles, selection)
    profile = profiles[selection.profile]
    named = set(selection.include) | set(selection.exclude)
    if profile.skills is not None:
        named |= set(profile.skills)
    return Resolution(
        skills=[skill for skill in skills if skill.name in wanted],
        dropped=sorted(set(catalog) - wanted),
        unknown=sorted(named - set(catalog)),
    )


def selected_skills(root: Path) -> list[Skill]:
    """Discovered skills filtered by this repository's selection.

    What every generated view (native dirs, adapter listings, drift check) is
    built from. With no ``harness.toml`` it is exactly ``discover_skills``.
    """
    from ml_python_base.skills_sync.discovery import discover_skills

    skills = discover_skills(root)
    selection = load_selection(root)
    if selection.is_default:
        return skills
    return resolve_skills(skills, load_profiles(root), selection).skills


def disabled_for(
    catalog: list[str], profiles: dict[str, Profile], profile_name: str
) -> list[str]:
    """The ``skills_disabled`` list that turns the full catalog into a profile.

    How the lab runs a profile as an experiment arm: no new run field, no new
    condition-hash input — a profile is a named, reproducible ablation.
    """
    wanted = wanted_names(catalog, profiles, Selection(profile=profile_name))
    return sorted(set(catalog) - wanted)


def write_selection(root: Path, profile: str) -> Path:
    """Write a fresh ``harness.toml`` selecting ``profile`` (bootstrap use)."""
    path = root / SELECTION_FILE
    path.write_text(
        "# Which governed skills this project carries (ADR-0085).\n"
        "# Profiles are defined in .github/skill-profiles.toml. Skills outside the\n"
        "# selection stay in .github/skills*/ but are not linked or listed for the\n"
        "# agent. Change this file, then run `make sync-skills`.\n"
        "[skills]\n"
        f'profile = "{profile}"\n'
        "include = []   # add skills on top of the profile\n"
        "exclude = []   # drop skills the profile carries\n",
        encoding="utf-8",
    )
    return path
