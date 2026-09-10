"""The evidence a claim-mapped audit reads from (ADR-0058, phase 2).

The talos audit could not see what its harness *did*: 700 characters of a
skill, hook paths without contents, no installer, no architecture document,
and no idea whether the suite was green at the pin. Each test here pins one
of those gaps closed, with the same tree-as-strings fixtures the rest of the
audit tests use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_lab_app import repository_audit_service as svc
from harness_lab_app.gate_check_service import (
    build_health_spec,
    health_at_pin,
    parse_suite_counts,
)
from harness_lab_app.models import TargetRepository
from harness_lab_app.runner_capabilities import SERVICE_NEEDS, service_capabilities


def _target(**over) -> TargetRepository:
    base = {
        "target_id": "talos",
        "name": "talos",
        "repository_url": "https://example.invalid/talos.git",
        "pinned_commit": "3" * 40,
        "validation_commands": [
            'for t in tests/test_*.sh; do bash "$t" || exit 1; done'
        ],
    }
    base.update(over)
    return TargetRepository(**base)


LONG_SKILL = (
    "---\nname: talos-plan\ndescription: Decompose SPEC.md into ordered tasks.\n---\n"
    "# /talos-plan\n\n" + ("Every task compiles and leaves the suite green. " * 40)
)

TALOS_LIKE = {
    "README.md": "# talos\nTakes a ticket, produces a PR.\n",
    "CLAUDE.md": "# Working on talos\nRead docs/ARCHITECTURE.md first.\n",
    "AGENTS.md": "# Rules\n",
    "docs/ARCHITECTURE.md": "# Two layers\nJudgement is the model's; certification is code's.\n",
    "skills/talos-plan/SKILL.md": LONG_SKILL,
    "skills/talos-verify/SKILL.md": "---\nname: talos-verify\ndescription: Run the gates.\n---\n",
    "hooks/armed-gate.sh": "#!/usr/bin/env bash\n# refuse git push until the run is armed\nexit 2\n",
    "hooks/test-guard.sh": "#!/usr/bin/env bash\nexit 0\n",
    "hooks/talos-pr-policy.yml": "policy: strict\n",
    "scripts/talos-install.sh": '#!/usr/bin/env bash\ncp settings.template.json "$1/.claude/settings.json"\n',
    "settings.template.json": '{"permissions": {"allow": ["Read"]}}',
    "lib/classify.sh": "#!/usr/bin/env bash\n",
    "tests/test_boot.sh": "#!/usr/bin/env bash\n",
}


def _evidence(files: dict[str, str], **kw):
    return svc.collect_evidence(
        _target(), "3" * 40, sorted(files), lambda p: files.get(p, ""), **kw
    )


# --------------------------------------------------------------------------
# 2.1 — fuller sources
# --------------------------------------------------------------------------


def test_a_skill_is_read_past_its_frontmatter():
    evidence = _evidence(TALOS_LIKE)
    body = evidence["skills"]["skills/talos-plan/SKILL.md"]
    assert len(body) > 700
    assert "leaves the suite green" in body


def test_hook_scripts_carry_their_text_not_only_their_names():
    hooks = _evidence(TALOS_LIKE)["hooks"]
    assert "armed-gate.sh" in hooks["claude_hook_scripts"]
    assert "refuse git push" in hooks["scripts_text"]["armed-gate.sh"]
    # The policy file beside them is not a hook and is not read as one.
    assert "talos-pr-policy.yml" not in hooks["scripts_text"]


def test_installers_and_settings_templates_are_found_anywhere_in_the_tree():
    installers = _evidence(TALOS_LIKE)["installers"]
    assert set(installers) == {"scripts/talos-install.sh", "settings.template.json"}
    assert "settings.template.json" in installers["scripts/talos-install.sh"]


def test_the_architecture_document_the_readme_points_at_is_read():
    doc = _evidence(TALOS_LIKE)["architecture_doc"]
    assert doc["path"] == "docs/ARCHITECTURE.md"
    assert "certification is code's" in doc["text"]


def test_a_tree_without_those_files_has_empty_slots_not_missing_keys():
    evidence = _evidence({"README.md": "# x\n", "CLAUDE.md": "# r\n"})
    assert evidence["installers"] == {}
    assert evidence["architecture_doc"] == {}
    assert evidence["hooks"]["scripts_text"] == {}
    assert evidence["lab_harness"] == {"skills": []}
    assert evidence["health_at_pin"]["status"] == "unmeasured"


def test_evidence_size_is_a_number_the_task_can_log():
    assert svc.evidence_chars(_evidence(TALOS_LIKE)) > 1000


# --------------------------------------------------------------------------
# 2.2 — the lab's own catalogue
# --------------------------------------------------------------------------


def test_the_lab_catalogue_reads_internal_and_external_skills(tmp_path: Path):
    (tmp_path / ".github" / "skills").mkdir(parents=True)
    (tmp_path / ".github" / "skills-external" / "writing-plans").mkdir(parents=True)
    (tmp_path / ".github" / "skills" / "README.md").write_text("# not a skill\n")
    (tmp_path / ".github" / "skills" / "verify_changes.md").write_text(
        "---\nname: verify_changes\ndescription: Run the gate.\n---\n# body\n"
    )
    (
        tmp_path / ".github" / "skills-external" / "writing-plans" / "SKILL.md"
    ).write_text("---\nname: writing-plans\ndescription: Plan before code\n---\n")
    rows = svc.lab_skill_catalogue(tmp_path)
    assert rows == [
        {"name": "verify_changes", "description": "Run the gate."},
        {"name": "writing-plans", "description": "Plan before code"},
    ]


def test_the_real_catalogue_names_the_skills_the_injected_arm_gets():
    names = {row["name"] for row in svc.lab_skill_catalogue()}
    assert {"verify_changes", "plan_and_execute_feature", "writing-plans"} <= names


def test_the_catalogue_enters_the_evidence_under_lab_harness():
    evidence = _evidence(
        TALOS_LIKE, lab_catalogue=[{"name": "writing-plans", "description": ""}]
    )
    assert evidence["lab_harness"]["skills"][0]["name"] == "writing-plans"


# --------------------------------------------------------------------------
# 2.3 — health at the pin
# --------------------------------------------------------------------------


@pytest.fixture
def lab_root(monkeypatch, tmp_path: Path) -> Path:
    from harness_lab_app import checkout_service, config

    settings = config.Settings(repo_root=tmp_path)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(checkout_service, "get_settings", lambda: settings)
    return tmp_path


def test_health_with_no_runnable_commands_is_an_empty_spec_not_an_error(lab_root):
    spec = build_health_spec(_target(validation_commands=[]))
    assert spec["commands"] == []
    assert health_at_pin([], measured=False)["status"] == "unmeasured"


def test_health_with_a_gate_provisions_and_validates(lab_root):
    spec = build_health_spec(_target())
    assert [c["phase"] for c in spec["commands"]] == ["provision", "validate"]


def test_counts_parse_from_a_talos_shaped_tail_and_not_from_an_unfamiliar_one():
    tail = "  ok 1\n  ok 2\n  20 passed, 1 failed\n"
    assert parse_suite_counts(tail) == {"passed": 20, "failed": 1}
    assert parse_suite_counts("1342 tests ran; see above") is None
    # The last summary line wins: per-file lines are followed by the total.
    assert parse_suite_counts("3 passed, 0 failed\n1342 passed, 39 failed\n") == {
        "passed": 1342,
        "failed": 39,
    }


def test_a_red_suite_is_fail_with_its_counts():
    results = [
        {"phase": "provision", "command": "clone", "exit_code": 0, "output": ""},
        {
            "phase": "validate",
            "command": "suite",
            "exit_code": 1,
            "output": "1342 passed, 39 failed",
        },
    ]
    health = health_at_pin(results, measured=True)
    assert health["status"] == "fail"
    assert health["counts"] == {"passed": 1342, "failed": 39}


def test_a_broken_provision_is_unmeasured_never_fail():
    results = [
        {"phase": "provision", "command": "clone", "exit_code": 128, "output": "boom"}
    ]
    health = health_at_pin(results, measured=True)
    assert health["status"] == "unmeasured"
    assert "provision" in health["reason"]


def test_a_green_suite_is_pass_and_counts_may_be_absent():
    results = [
        {"phase": "provision", "command": "clone", "exit_code": 0, "output": ""},
        {"phase": "validate", "command": "suite", "exit_code": 0, "output": "all good"},
    ]
    health = health_at_pin(results, measured=True)
    assert health["status"] == "pass"
    assert health["counts"] is None


# --------------------------------------------------------------------------
# 2.4 — service capabilities, declared not probed
# --------------------------------------------------------------------------


def test_service_capabilities_cover_every_need_and_say_what_decided_them():
    table = service_capabilities(execution_backend="docker", env=lambda _name: "")
    assert set(table) == set(SERVICE_NEEDS)
    assert table["docker"]["available"] is True
    assert table["jira"]["available"] is False
    assert table["target_repo"]["available"] is False
    assert all(row["source"] == "declared" for row in table.values())
    assert "JIRA_API_TOKEN" in table["jira"]["reason"]


def test_a_token_in_the_environment_turns_a_service_on():
    env = {"GITHUB_TOKEN": "ghp_x", "BROWSER_AVAILABLE": "true"}
    table = service_capabilities(
        execution_backend="subprocess", env=lambda n: env.get(n, "")
    )
    assert table["github"]["available"] is True
    assert table["browser"]["available"] is True
    assert table["docker"]["available"] is False
