"""A run picks its harness level by profile name: full or minimal (ADR-0085)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness_lab_app.schemas import RunBatchCreate, RunConfigCreate
from harness_lab_app.skills_catalog import (
    known_skill_names,
    profile_disabled,
    skill_profiles,
)


def _config(**over) -> RunConfigCreate:
    return RunConfigCreate(case_id="tetris-v2", prompt_variant="harness", **over)


def test_the_governed_profiles_are_full_and_minimal_and_name_real_skills():
    profiles = skill_profiles()
    assert set(profiles) == {"full", "minimal"}
    catalog = known_skill_names()
    assert profiles["full"]["disabled"] == []
    assert set(profiles["minimal"]["skills"]) <= catalog
    # The working loop every case's harness prompt drives is always carried.
    assert {
        "plan_and_execute_feature",
        "systematic_debugging",
        "verify_changes",
    } <= set(profiles["minimal"]["skills"])
    # Minimal hides the rest: nothing is lost, nothing is carried twice.
    assert (
        set(profiles["minimal"]["skills"]) | set(profiles["minimal"]["disabled"])
        == catalog
    )


def test_a_minimal_run_records_the_exact_skills_it_hides():
    config = _config(skill_profile="minimal")
    assert config.skills_disabled == profile_disabled("minimal")
    assert config.skills_disabled  # minimal hides something


def test_full_and_no_profile_are_the_same_full_harness():
    assert _config(skill_profile="full").skills_disabled == []
    assert _config().skills_disabled == []


def test_an_unknown_profile_is_refused():
    with pytest.raises(ValidationError, match="Unknown skill profile"):
        _config(skill_profile="tiny")


def test_a_profile_and_a_different_hand_list_are_exclusive():
    with pytest.raises(ValidationError, match="exclusive"):
        _config(skill_profile="minimal", skills_disabled=["verify_changes"])
    # The same list the profile expands to is not a conflict (a re-submitted form).
    same = profile_disabled("minimal")
    assert (
        _config(skill_profile="minimal", skills_disabled=same).skills_disabled == same
    )


def test_a_batch_carries_the_profile_to_every_run():
    batch = RunBatchCreate(
        case_ids=["tetris-v2", "chess3d-v1"],
        prompt_variants=["harness"],
        skill_profile="minimal",
    )
    assert batch.skills_disabled == profile_disabled("minimal")
