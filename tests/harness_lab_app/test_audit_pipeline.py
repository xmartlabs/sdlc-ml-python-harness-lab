"""From a parsed map to cases the lab can run (ADR-0058, phases 3 and 4).

The model proposes; these tests pin what the lab does with the proposal —
which rows it declares evaluable, which counterparts it refuses, which cases
it drops and why, which experiment arms it derives — and that the whole
two-pass task stores a report with those sections, for talos and for an
audit recorded under the old contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_lab_app import repository_audit_service as svc
from harness_lab_app.audit_contract import (
    annotate_cases,
    annotate_map,
    derive_conditions,
    parse_case_proposals,
    parse_component_map,
)
from harness_lab_app.models import TargetRepository
from harness_lab_app.tasks import _audit_repository_body

FIXTURES = Path(__file__).parent / "fixtures"
LAB_SKILLS = [
    "brainstorming",
    "requesting-code-review",
    "writing-plans",
    "subagent-driven-development",
    "verify_changes",
    "test-driven-development",
    "finishing-a-development-branch",
    "generate_implementation_docs",
    "plan_and_execute_feature",
]
NO_SERVICES = {
    need: {"available": False, "reason": f"no {need} here"}
    for need in ("jira", "github", "browser", "target_repo")
} | {
    "docker": {"available": True, "reason": "docker backend"},
    "network": {"available": True, "reason": "egress"},
}


@pytest.fixture
def talos_map() -> dict:
    parsed = parse_component_map(
        (FIXTURES / "audit_talos_component_map.json").read_text()
    )
    return annotate_map(
        parsed,
        lab_skill_names=LAB_SKILLS,
        services=NO_SERVICES,
        installer_paths=["scripts/talos-install.sh", "settings.template.json"],
    )


@pytest.fixture
def talos_cases(talos_map) -> dict:
    return annotate_cases(
        parse_case_proposals((FIXTURES / "audit_talos_cases.json").read_text()),
        talos_map,
    )


# --------------------------------------------------------------------------
# Annotation — facts fill the fields the model may not write
# --------------------------------------------------------------------------


def test_evaluability_comes_from_needs_against_declared_services(talos_map):
    by_name = {row["name"]: row for row in talos_map["component_map"]}
    assert by_name["talos-plan"]["evaluable_here"] is True
    assert by_name["talos-ticket"]["evaluable_here"] is False
    assert "jira" in by_name["talos-ticket"]["evaluable_reason"]
    # docker is on, browser is not: the row is blocked by the one that is off.
    assert by_name["talos-qa"]["evaluable_here"] is False
    assert "browser" in by_name["talos-qa"]["evaluable_reason"]
    assert "docker" not in by_name["talos-qa"]["evaluable_reason"]
    evaluable = {name for name, row in by_name.items() if row["evaluable_here"]}
    assert {
        "talos-spec",
        "talos-plan",
        "talos-implement",
        "talos-verify",
        "talos-report",
    } <= evaluable


def test_a_counterpart_outside_the_catalogue_is_cleared_with_a_warning():
    parsed = parse_component_map(
        json.dumps(
            {
                "component_map": [
                    {"kind": "skill", "name": "x", "lab_counterpart": "verify_changes"},
                    {"kind": "skill", "name": "y", "lab_counterpart": "made-up-skill"},
                ],
                "shape": {"shape": "in_place"},
            }
        )
    )
    annotated = annotate_map(
        parsed, lab_skill_names=["verify_changes"], services=NO_SERVICES
    )
    rows = {row["name"]: row for row in annotated["component_map"]}
    assert rows["x"]["counterpart_valid"] is True
    assert rows["y"]["lab_counterpart"] == ""
    assert rows["y"]["counterpart_valid"] is False
    assert any("made-up-skill" in w for w in annotated["warnings"])


def test_the_install_entry_is_checked_against_the_installers_found(talos_map):
    assert talos_map["shape"]["install_entry_exists"] is True
    other = annotate_map(
        {"component_map": [], "shape": {"install_entry": "scripts/nope.sh"}},
        lab_skill_names=[],
        services=NO_SERVICES,
        installer_paths=[],
    )
    assert other["shape"]["install_entry_exists"] is False
    assert any("scripts/nope.sh" in w for w in other["warnings"])


# --------------------------------------------------------------------------
# Cases — kept, dropped, and the arms each kept one implies
# --------------------------------------------------------------------------


def test_cases_are_kept_or_dropped_with_the_reason_on_the_row(talos_cases):
    by_title = {case["title"]: case for case in talos_cases["cases"]}
    assert by_title["Ticket en prosa → SPEC.md y plan.json"]["dropped_reason"] == ""
    pr = by_title["Abrir el PR draft"]
    assert "talos-pr" in pr["dropped_reason"]
    assert "github" in pr["dropped_reason"]
    assert "conditions" not in pr
    # The evidence case exercises talos-evidence (docker: on) and classify.sh.
    assert by_title["Bug plantado → evidencia red→green"]["dropped_reason"] == ""


def test_a_case_naming_no_component_or_an_unknown_one_is_dropped(talos_map):
    raw = json.dumps(
        {
            "cases": [
                {"title": "orphan", "measures": "harness_claim"},
                {
                    "title": "ghost",
                    "exercises": ["talos-teleport"],
                    "measures": "harness_claim",
                },
            ]
        }
    )
    cases = annotate_cases(parse_case_proposals(raw), talos_map)["cases"]
    assert cases[0]["dropped_reason"] == "names no component of the map"
    assert "talos-teleport" in cases[1]["dropped_reason"]


def test_skills_derive_the_three_standard_arms(talos_cases):
    case = next(
        c for c in talos_cases["cases"] if c["title"].startswith("Ticket en prosa")
    )
    labels = [arm["label"] for arm in case["conditions"]]
    assert labels == ["native-on", "native-off", "lab-counterpart"]
    by_label = {arm["label"]: arm for arm in case["conditions"]}
    assert by_label["native-on"] == {
        "label": "native-on",
        "role": "control",
        "overrides": {},
    }
    assert by_label["native-off"]["overrides"] == {
        "skills_disabled": ["talos-spec", "talos-plan"]
    }
    assert by_label["lab-counterpart"]["overrides"] == {
        "skills_disabled": ["talos-spec", "talos-plan"],
        "skills_extra": ["brainstorming", "writing-plans"],
    }
    assert case["conditions_note"] == ""


def test_a_skill_without_a_counterpart_gets_only_the_ablation_arm(talos_map):
    case = {"exercises": ["talos-evidence"]}
    derived = derive_conditions(case, talos_map)
    assert [arm.label for arm in derived["conditions"]] == ["native-on", "native-off"]
    assert "no valid lab counterpart" in derived["note"]


def test_a_case_with_no_skill_gets_the_native_injected_pair(talos_map):
    derived = derive_conditions(
        {"exercises": ["armed-gate.sh", "CLAUDE.md"]}, talos_map
    )
    assert [arm.to_dict() for arm in derived["conditions"]] == [
        {
            "label": "native",
            "role": "control",
            "overrides": {"harness_source": "native"},
        },
        {
            "label": "injected",
            "role": "treatment",
            "overrides": {"harness_source": "injected"},
        },
    ]
    assert "cannot be switched per arm" in derived["note"]


# --------------------------------------------------------------------------
# The task, end to end, with the talos fixtures as the model
# --------------------------------------------------------------------------


def _seed(session, **over) -> TargetRepository:
    base = {
        "target_id": "talos",
        "name": "talos",
        "repository_url": "git@github.com:xmartlabs/talos.git",
        "pinned_commit": "3" * 40,
        "validation_commands": [
            'for t in tests/test_*.sh; do bash "$t" || exit 1; done'
        ],
    }
    base.update(over)
    target = TargetRepository(**base)
    session.add(target)
    session.commit()
    return target


TALOS_TREE = {
    "README.md": "# talos\n",
    "CLAUDE.md": "# Working on talos\n",
    "AGENTS.md": "# Rules\n",
    "skills/talos-plan/SKILL.md": "---\nname: talos-plan\n---\nbody\n",
    "hooks/armed-gate.sh": "exit 2\n",
    "scripts/talos-install.sh": "cp settings.template.json x\n",
    "settings.template.json": "{}",
    "tests/test_boot.sh": "",
}
RED_SUITE = {
    "status": "fail",
    "reason": "",
    "counts": {"passed": 1342, "failed": 39},
    "checks": [],
}


def _talos_evidence(target, **kw):
    return svc.collect_evidence(
        target,
        "3" * 40,
        sorted(TALOS_TREE),
        lambda p: TALOS_TREE.get(p, ""),
        lab_catalogue=[{"name": n, "description": ""} for n in LAB_SKILLS],
        health_at_pin=kw.get("health_at_pin"),
    )


def _fixture_gateway(seen: list[str]):
    answers = {
        svc.COMPONENT_MAP_PROMPT: (
            FIXTURES / "audit_talos_component_map.json"
        ).read_text(),
        svc.CASE_PROPOSALS_PROMPT: (FIXTURES / "audit_talos_cases.json").read_text(),
    }

    def gateway(model: str, system: str, user: str) -> str:
        seen.append(user)
        return answers[system]

    return gateway


def test_the_two_pass_task_stores_a_claim_mapped_report(session, monkeypatch):
    _seed(session)
    monkeypatch.setattr(svc, "gather_evidence", _talos_evidence)
    monkeypatch.setattr(
        "harness_lab_app.runner_capabilities.service_capabilities", lambda: NO_SERVICES
    )
    users: list[str] = []

    _audit_repository_body(
        session, "talos", "planner-x", gateway=_fixture_gateway(users), health=RED_SUITE
    )

    audit = svc.RepositoryAuditService(session).get("talos")
    assert audit is not None and audit.status == "done", audit.error
    findings = audit.findings_json
    assert findings["shape"]["shape"] == "installable"
    assert findings["shape"]["install_entry_exists"] is True
    assert len(findings["component_map"]) == 17
    kept = [c for c in findings["cases"] if not c["dropped_reason"]]
    dropped = [c for c in findings["cases"] if c["dropped_reason"]]
    assert len(kept) == 3 and len(dropped) == 1
    assert kept[0]["conditions"][2]["label"] == "lab-counterpart"
    # The second pass saw the annotated map and the red suite.
    assert '"evaluable_here": false' in users[1]
    assert '"status": "fail"' in users[1]
    # The report has the new sections, in order, and the lab's verdict last.
    md = audit.markdown
    for a, b in zip(
        [
            "## Forma del harness",
            "## Mapa de componentes",
            "## Casos propuestos",
            "## Veredicto",
        ],
        ["## Mapa de componentes", "## Casos propuestos", "## Veredicto", ""],
        strict=False,
    ):
        assert a in md
        if b:
            assert md.index(a) < md.index(b)
    assert "se instala en otro proyecto" in md
    assert "1342 pasan, 39 fallan" in md
    assert "_Descartado:_" in md
    assert "`lab-counterpart`" in md
    # The runner match still runs, on the kept cases only.
    assert audit.evidence_json["case_plan"]["cases"][0]["status"] == "runnable"
    assert len(audit.evidence_json["case_plan"]["cases"]) == 3
    assert audit.evidence_json["services"]["jira"]["available"] is False


def test_a_second_pass_that_is_not_json_keeps_the_map_and_says_so(session, monkeypatch):
    _seed(session)
    monkeypatch.setattr(svc, "gather_evidence", _talos_evidence)
    monkeypatch.setattr(
        "harness_lab_app.runner_capabilities.service_capabilities", lambda: NO_SERVICES
    )
    good_map = (FIXTURES / "audit_talos_component_map.json").read_text()

    def gateway(model: str, system: str, user: str) -> str:
        return (
            good_map if system == svc.COMPONENT_MAP_PROMPT else "I would run the tests."
        )

    _audit_repository_body(session, "talos", "m", gateway=gateway, health=RED_SUITE)

    audit = svc.RepositoryAuditService(session).get("talos")
    assert audit.status == "done"
    assert len(audit.findings_json["component_map"]) == 17
    assert audit.findings_json["cases"] == []
    assert any(w.startswith("cases:") for w in audit.findings_json["warnings"])


def test_an_audit_stored_under_the_old_contract_still_renders():
    old_findings = {
        "summary": "Un harness.",
        "harness_verdict": "partial",
        "harness_components": [{"kind": "skill", "name": "verify", "assessment": "ok"}],
        "language_notes": "",
        "standards": [],
        "gaps": [],
        "use_cases": [
            {"title": "Caso", "why": "porque", "validation_hint": "make test"}
        ],
    }
    evidence = {
        "target_id": "old",
        "runtime": {},
        "harness_present": True,
        "next_step": "continue",
    }
    md = svc.render_markdown(old_findings, evidence, model="m")
    assert "## Casos de uso posibles" in md
    assert "## Mapa de componentes" not in md
    assert "## Forma del harness" not in md
