"""The claim-mapped audit contract parses what the model says and nothing more.

The fixture is a hand-written model answer for talos at ``303ffc6a``, the
repository whose audit produced a case that measured nothing (ADR-0058). It
is the specification the prompts of later phases must satisfy: sixteen
skills, two hooks, one deterministic command, two rules files, and a shape
that says the harness is installed into another project.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_lab_app.audit_contract import (
    CASE_MEASURES,
    COMPONENT_KINDS,
    HARNESS_SHAPES,
    NEEDS_VOCABULARY,
    UNKNOWN,
    CaseProposal,
    ComponentRow,
    DerivedCondition,
    HarnessShape,
    parse_case_proposals,
    parse_component_map,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def talos_map_text() -> str:
    return (FIXTURES / "audit_talos_component_map.json").read_text()


@pytest.fixture
def talos_cases_text() -> str:
    return (FIXTURES / "audit_talos_cases.json").read_text()


# --------------------------------------------------------------------------
# Pass 1 — the component map
# --------------------------------------------------------------------------


def test_the_talos_map_parses_without_a_single_warning(talos_map_text):
    parsed = parse_component_map(talos_map_text)
    assert "parse_error" not in parsed
    assert parsed["warnings"] == []
    assert parsed["harness_verdict"] == "complete"
    assert len(parsed["component_map"]) == 17
    kinds = {row["kind"] for row in parsed["component_map"]}
    assert kinds == {"rule", "hook", "command", "skill"}
    assert kinds <= set(COMPONENT_KINDS)


def test_the_map_keeps_what_the_model_read_and_where(talos_map_text):
    parsed = parse_component_map(talos_map_text)
    by_name = {row["name"]: row for row in parsed["component_map"]}
    plan = by_name["talos-plan"]
    assert plan["source_path"] == "skills/talos-plan/SKILL.md"
    assert "suite verde" in plan["claim"]
    assert plan["lab_counterpart"] == "writing-plans"
    assert plan["needs"] == ["none"]
    assert by_name["talos-ticket"]["needs"] == ["jira"]
    assert by_name["talos-qa"]["needs"] == ["docker", "browser"]
    assert by_name["classify.sh"]["kind"] == "command"


def test_lab_owned_fields_start_neutral_whatever_the_model_wrote():
    raw = json.dumps(
        {
            "component_map": [
                {
                    "kind": "skill",
                    "name": "x",
                    "needs": ["jira"],
                    "evaluable_here": True,
                    "evaluable_reason": "trust me",
                    "counterpart_valid": True,
                }
            ]
        }
    )
    row = parse_component_map(raw)["component_map"][0]
    assert row["evaluable_here"] is False
    assert row["evaluable_reason"] == ""
    assert row["counterpart_valid"] is False


def test_the_shape_says_installable_and_names_its_entry(talos_map_text):
    shape = parse_component_map(talos_map_text)["shape"]
    assert shape["shape"] == "installable"
    assert shape["shape"] in HARNESS_SHAPES
    assert shape["install_entry"] == "scripts/talos-install.sh"
    assert "ticket" in shape["unit_of_work"]
    # Lab-owned until the annotation step checks the path against the tree.
    assert shape["install_entry_exists"] is False


def test_a_need_outside_the_vocabulary_is_kept_as_unknown_with_a_warning():
    raw = json.dumps(
        {
            "component_map": [
                {"kind": "skill", "name": "x", "needs": ["telepathy", "jira"]}
            ]
        }
    )
    parsed = parse_component_map(raw)
    row = parsed["component_map"][0]
    assert row["needs"] == [UNKNOWN, "jira"]
    assert "telepathy" in parsed["warnings"][0]
    assert parsed["warnings"][1] == "shape: missing or not an object"
    assert len(parsed["warnings"]) == 2
    assert all(need in NEEDS_VOCABULARY for need in ("jira", "none"))


def test_none_beside_a_real_need_is_a_contradiction_the_real_need_wins():
    raw = json.dumps(
        {"component_map": [{"kind": "hook", "name": "h", "needs": ["none", "docker"]}]}
    )
    assert parse_component_map(raw)["component_map"][0]["needs"] == ["docker"]


def test_a_kind_outside_the_vocabulary_keeps_the_row():
    raw = json.dumps(
        {"component_map": [{"kind": "plugin", "name": "p", "claim": "does things"}]}
    )
    parsed = parse_component_map(raw)
    assert parsed["component_map"][0]["kind"] == UNKNOWN
    assert parsed["component_map"][0]["claim"] == "does things"
    assert "plugin" in parsed["warnings"][0]


def test_a_row_without_a_name_is_dropped_and_said_so():
    raw = json.dumps(
        {"component_map": [{"kind": "skill", "claim": "nameless"}, "not an object"]}
    )
    parsed = parse_component_map(raw)
    assert parsed["component_map"] == []
    assert parsed["warnings"][:2] == [
        "component_map[0]: no usable name, dropped",
        "component_map[1]: not an object, dropped",
    ]


def test_a_counterpart_that_is_not_a_name_is_cleared():
    raw = json.dumps(
        {
            "component_map": [
                {
                    "kind": "skill",
                    "name": "x",
                    "lab_counterpart": "the plans skill, I think",
                }
            ]
        }
    )
    assert parse_component_map(raw)["component_map"][0]["lab_counterpart"] == ""


def test_prose_instead_of_json_is_a_parse_error_not_an_invented_map():
    parsed = parse_component_map("This repository has a fine harness.")
    assert "parse_error" in parsed
    assert "component_map" not in parsed


def test_a_fenced_answer_is_read_through_the_fence():
    raw = (
        "```json\n"
        + json.dumps({"component_map": [{"kind": "rule", "name": "CLAUDE.md"}]})
        + "\n```"
    )
    assert parse_component_map(raw)["component_map"][0]["name"] == "CLAUDE.md"


# --------------------------------------------------------------------------
# Pass 2 — case proposals
# --------------------------------------------------------------------------


def test_the_talos_cases_parse_without_a_warning(talos_cases_text):
    parsed = parse_case_proposals(talos_cases_text)
    assert "parse_error" not in parsed
    assert parsed["warnings"] == []
    assert len(parsed["cases"]) == 4
    assert {case["measures"] for case in parsed["cases"]} == set(CASE_MEASURES)


def test_a_case_names_the_components_it_exercises(talos_cases_text):
    first = parse_case_proposals(talos_cases_text)["cases"][0]
    assert first["exercises"] == ["talos-spec", "talos-plan"]
    assert first["measures"] == "harness_claim"
    assert first["required_runtime_version"] == "3.2"
    assert first["dropped_reason"] == ""


def test_a_case_with_no_exercises_is_kept_for_the_annotation_step_to_judge():
    raw = json.dumps({"cases": [{"title": "orphan", "measures": "harness_claim"}]})
    case = parse_case_proposals(raw)["cases"][0]
    assert case["exercises"] == []
    assert case["dropped_reason"] == ""


def test_a_measures_outside_the_vocabulary_is_unknown_with_a_warning():
    raw = json.dumps({"cases": [{"title": "t", "measures": "vibes"}]})
    parsed = parse_case_proposals(raw)
    assert parsed["cases"][0]["measures"] == UNKNOWN
    assert "vibes" in parsed["warnings"][0]


def test_a_case_without_a_title_is_dropped():
    parsed = parse_case_proposals(json.dumps({"cases": [{"exercises": ["x"]}]}))
    assert parsed["cases"] == []
    assert "no title" in parsed["warnings"][0]


def test_prose_instead_of_json_is_a_parse_error_for_cases_too():
    assert "parse_error" in parse_case_proposals("Run the tests.")


# --------------------------------------------------------------------------
# The rows as code — what later phases build on
# --------------------------------------------------------------------------


def test_rows_round_trip_through_to_dict():
    row = ComponentRow(
        kind="skill",
        name="talos-plan",
        source_path="skills/talos-plan/SKILL.md",
        claim="c",
        trigger="t",
        observable="o",
        lab_counterpart="writing-plans",
        needs=("none",),
    )
    assert row.to_dict()["needs"] == ["none"]
    case = CaseProposal(
        title="t",
        exercises=("a",),
        measures="harness_claim",
        task="",
        observable="",
        validation_hint="",
    )
    assert case.to_dict()["exercises"] == ["a"]
    assert (
        HarnessShape(shape="in_place", install_entry="", unit_of_work="").to_dict()[
            "shape"
        ]
        == "in_place"
    )


def test_a_derived_condition_speaks_the_overrides_vocabulary():
    arm = DerivedCondition(
        label="native-off",
        role="treatment",
        overrides={"skills_disabled": ["talos-plan"]},
    )
    data = arm.to_dict()
    assert data == {
        "label": "native-off",
        "role": "treatment",
        "overrides": {"skills_disabled": ["talos-plan"]},
    }
    # A deep copy: an arm handed to the runner cannot be edited back.
    data["overrides"]["skills_disabled"].append("x")
    assert arm.overrides["skills_disabled"] == ["talos-plan"]
