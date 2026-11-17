"""Skill profiles filter the generated views, never the governed sources (ADR-0085)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ml_python_base.skills_sync.cli import check_drift, main
from ml_python_base.skills_sync.config import load_registry
from ml_python_base.skills_sync.discovery import discover_skills
from ml_python_base.skills_sync.profiles import (
    FULL,
    ProfileError,
    Selection,
    disabled_for,
    load_profiles,
    load_selection,
    resolve_skills,
    selected_skills,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "adapters/registry.toml"

PROFILES = """
[profile.minimal]
description = "loop"
skills = ["alpha"]

[profile.core]
extends = "minimal"
skills = ["beta", "ghost"]
"""


def _seed(root: Path, profiles: str = PROFILES) -> None:
    internal = root / ".github/skills"
    internal.mkdir(parents=True)
    for name in ("alpha", "beta", "gamma"):
        (internal / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: does {name}\n---\n", encoding="utf-8"
        )
    (root / ".github/skills-external").mkdir(parents=True)
    if profiles:
        (root / ".github/skill-profiles.toml").write_text(profiles, encoding="utf-8")


def _select(root: Path, body: str) -> None:
    (root / "harness.toml").write_text(f"[skills]\n{body}", encoding="utf-8")


def test_full_is_built_in_and_extends_resolves_transitively(tmp_path: Path) -> None:
    _seed(tmp_path)
    profiles = load_profiles(tmp_path)
    assert profiles[FULL].skills is None
    assert profiles["core"].skills == {"alpha", "beta", "ghost"}


def test_without_a_selection_every_skill_is_carried(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert load_selection(tmp_path).is_default
    assert [s.name for s in selected_skills(tmp_path)] == ["alpha", "beta", "gamma"]


def test_a_selection_applies_profile_then_include_then_exclude(tmp_path: Path) -> None:
    _seed(tmp_path)
    skills = discover_skills(tmp_path)
    selection = Selection(profile="core", include=("gamma",), exclude=("alpha",))
    resolution = resolve_skills(skills, load_profiles(tmp_path), selection)
    assert [s.name for s in resolution.skills] == ["beta", "gamma"]
    assert resolution.dropped == ["alpha"]
    # A name the catalog no longer has is reported, not fatal: a template sync
    # may retire a skill and the project must still build.
    assert resolution.unknown == ["ghost"]


def test_an_unknown_profile_is_refused(tmp_path: Path) -> None:
    _seed(tmp_path)
    _select(tmp_path, 'profile = "tiny"\n')
    with pytest.raises(ProfileError, match="tiny"):
        selected_skills(tmp_path)


def test_a_profile_cycle_is_refused(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        '[profile.a]\nextends = "b"\nskills = []\n[profile.b]\nextends = "a"\nskills = []\n',
    )
    with pytest.raises(ProfileError, match="cycle"):
        load_profiles(tmp_path)


def test_disabled_for_is_the_ablation_that_yields_the_profile(tmp_path: Path) -> None:
    _seed(tmp_path)
    profiles = load_profiles(tmp_path)
    assert disabled_for(["alpha", "beta", "gamma"], profiles, "minimal") == [
        "beta",
        "gamma",
    ]
    assert disabled_for(["alpha", "beta", "gamma"], profiles, FULL) == []


def test_sync_links_and_lists_only_the_selection_and_keeps_sources(
    tmp_path: Path,
) -> None:
    _seed(tmp_path)
    _select(tmp_path, 'profile = "minimal"\n')
    assert main(["--root", str(tmp_path), "--registry", str(REGISTRY), "sync"]) == 0

    linked = sorted(p.name for p in (tmp_path / ".claude/skills").iterdir())
    assert linked == ["alpha"]
    # Filtered, never deleted: the governed source stays complete.
    assert (tmp_path / ".github/skills/gamma.md").is_file()
    assert check_drift(tmp_path, load_registry(REGISTRY)) == []

    # Widening the selection is drift until the next sync — CI catches it.
    _select(tmp_path, 'profile = "minimal"\ninclude = ["gamma"]\n')
    stale = check_drift(tmp_path, load_registry(REGISTRY))
    assert ".agents/skills/.generated-manifest.tsv" in stale
    assert ".claude/skills" in stale
    assert main(["--root", str(tmp_path), "--registry", str(REGISTRY), "sync"]) == 0
    assert check_drift(tmp_path, load_registry(REGISTRY)) == []
    linked = sorted(p.name for p in (tmp_path / ".claude/skills").iterdir())
    assert linked == ["alpha", "gamma"]


def test_profile_set_writes_the_selection_and_relinks(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert main(["--root", str(tmp_path), "--registry", str(REGISTRY), "sync"]) == 0
    code = main(
        [
            "--root",
            str(tmp_path),
            "--registry",
            str(REGISTRY),
            "profile",
            "--set",
            "minimal",
        ]
    )
    assert code == 0
    assert load_selection(tmp_path).profile == "minimal"
    linked = sorted(p.name for p in (tmp_path / ".claude/skills").iterdir())
    assert linked == ["alpha"]


def test_the_lab_profiles_name_only_governed_skills() -> None:
    """The shipped definitions must not drift from the shipped catalog."""
    catalog = {skill.name for skill in discover_skills(REPO_ROOT)}
    for profile in load_profiles(REPO_ROOT).values():
        if profile.skills is not None:
            assert profile.skills <= catalog, profile.name
