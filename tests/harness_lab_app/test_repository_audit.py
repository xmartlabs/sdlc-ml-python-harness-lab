"""The LLM harness audit of a repository, before the lab runs anything.

What these tests pin is the split the feature rests on: the evidence and the
``next_step`` gate are the lab's, computed from the tree; the findings are the
model's, coerced to a fixed shape; and the report is rendered from both so a
model's words can never open a door a fact has closed. The model's prose itself
is never asserted on.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from harness_lab_app import (
    api as api_module,
    repository_audit_service as svc,
)
from harness_lab_app.api import create_app
from harness_lab_app.models import TargetRepository
from harness_lab_app.tasks import _audit_repository_body

# --------------------------------------------------------------------------
# Fixtures — trees as lists of paths plus a reader, no clone needed
# --------------------------------------------------------------------------


def _target(**over) -> TargetRepository:
    base = {
        "target_id": "payments",
        "name": "Payments API",
        "repository_url": "https://example.invalid/payments.git",
        "pinned_commit": "a" * 40,
        "validation_commands": ["make test"],
    }
    base.update(over)
    return TargetRepository(**base)


GOVERNED_PYTHON = {
    "pyproject.toml": "[project]\nname = 'payments'\n",
    "Makefile": "test:\n\tpytest\n\ncheck: test\n\truff check .\n.PHONY: test\n",
    "README.md": "# Payments\nBilling service.\n",
    "CLAUDE.md": "# Rules\nAlways run make test before finishing.\n",
    ".claude/settings.json": json.dumps(
        {"hooks": {"SessionStart": [{"hooks": []}], "Stop": [{"hooks": []}]}}
    ),
    ".claude/hooks/session_start.sh": "echo hi\n",
    ".claude/skills/verify/SKILL.md": "---\nname: verify\ndescription: run the gate\n---\n",
    ".claude/skills/plan/SKILL.md": "---\nname: plan\ndescription: plan first\n---\n",
    ".claude/agents/reviewer.md": "# Reviewer\nReviews diffs.\n",
    ".claude/commands/verify.md": "Run make check.\n",
    ".github/workflows/ci.yml": "name: ci\n",
    "src/payments/__init__.py": "",
    "tests/test_payments.py": "def test_ok(): pass\n",
}


def _evidence(files: dict[str, str], target: TargetRepository | None = None):
    paths = sorted(files)
    return svc.collect_evidence(
        target or _target(), "a" * 40, paths, lambda p: files.get(p, "")
    )


# --------------------------------------------------------------------------
# Evidence — read from the tree, verdicts from facts
# --------------------------------------------------------------------------


def test_a_governed_python_repository_can_continue():
    evidence = _evidence(GOVERNED_PYTHON)

    assert evidence["harness_present"] is True
    assert evidence["runtime"]["runner_can_execute"] is True
    assert evidence["runtime"]["supported"] == ["Python"]
    assert evidence["next_step"] == svc.NEXT_STEP_CONTINUE
    assert "Python" in evidence["next_step_reason"]


GOVERNED_NODE = {
    "package.json": json.dumps({"name": "checkout", "scripts": {"test": "vitest run"}}),
    "README.md": "# Checkout\n",
    "AGENTS.md": "# Rules\nRun npm test before finishing.\n",
    "src/index.ts": "export const x = 1;\n",
    "src/cart.ts": "export const y = 2;\n",
    "tests/cart.test.ts": "it('works', () => {});\n",
}


def test_a_governed_node_repository_can_continue():
    """Characterization: Node reaches `continue` exactly as it always has.

    Pinned before the capability model went in, so a regression in how the
    runner's abilities are computed shows up here as a Node repository that
    stopped being executable rather than as a subtle wording change.
    """
    evidence = _evidence(GOVERNED_NODE)

    assert evidence["harness_present"] is True
    assert evidence["runtime"]["runner_can_execute"] is True
    assert evidence["runtime"]["supported"] == ["Node.js"]
    assert evidence["runtime"]["unsupported"] == []
    assert evidence["next_step"] == svc.NEXT_STEP_CONTINUE
    assert "Node.js" in evidence["next_step_reason"]


def test_node_files_without_a_project_marker_still_count_as_node():
    files = {"scripts/build.mjs": "", "scripts/lint.mjs": "", "AGENTS.md": "# R\n"}
    evidence = _evidence(files)

    assert evidence["runtime"]["supported"] == ["Node.js"]
    assert evidence["runtime"]["detected_from_files_only"] is True
    assert evidence["next_step"] == svc.NEXT_STEP_CONTINUE


def test_the_evidence_carries_what_the_model_is_allowed_to_read():
    evidence = _evidence(GOVERNED_PYTHON)

    assert evidence["rules_files"]["CLAUDE.md"].startswith("# Rules")
    assert set(evidence["skills"]) == {
        ".claude/skills/verify/SKILL.md",
        ".claude/skills/plan/SKILL.md",
    }
    assert list(evidence["agents"]) == [".claude/agents/reviewer.md"]
    assert evidence["commands"] == ["verify"]
    assert evidence["makefile_targets"] == ["test", "check"]
    assert evidence["readme"].startswith("# Payments")
    assert evidence["validation_commands"] == ["make test"]


def test_hooks_are_read_from_claude_settings_and_the_hooks_directory():
    evidence = _evidence(GOVERNED_PYTHON)

    assert evidence["hooks"]["claude_settings_events"] == ["SessionStart", "Stop"]
    assert evidence["hooks"]["claude_hook_scripts"] == ["session_start.sh"]
    assert evidence["hooks"]["pre_commit"] is False


# A repository that keeps its harness at the root rather than under `.claude/`.
# talos does exactly this, and the audit used to report it as having none.
ROOT_LEVEL_SHELL = {
    "AGENTS.md": "# Rules\nRun the suite before finishing.\n",
    "CLAUDE.md": "# Rules\nSame, for Claude.\n",
    "README.md": "# talos\n",
    "Makefile": "test:\n\tbash tests/run.sh\n",
    "skills/talos-admit/SKILL.md": "---\nname: talos-admit\n---\n",
    "skills/talos-verify/SKILL.md": "---\nname: talos-verify\n---\n",
    "hooks/pre-push": "#!/usr/bin/env bash\n",
    "hooks/armed-gate.sh": "#!/usr/bin/env bash\n",
    "hooks/talos-pr-policy.yml": "policy: {}\n",
    "tests/test_parse.sh": "#!/usr/bin/env bash\n",
    "lib/evidence.sh": "#!/usr/bin/env bash\n",
}


def test_a_root_level_harness_is_seen_the_same_as_a_dotted_one():
    """`skills/` and `hooks/` at the root are a harness, not a blind spot.

    The taxonomy used to name only `.claude/`-style roots, so a repository with
    sixteen skills and four hooks was reported as having no harness at all —
    an answer worse than "unknown", because it read as a finding.
    """
    evidence = _evidence(ROOT_LEVEL_SHELL)

    assert evidence["governance"]["skills"] == ["talos-admit", "talos-verify"]
    assert sorted(evidence["skills"]) == [
        "skills/talos-admit/SKILL.md",
        "skills/talos-verify/SKILL.md",
    ]
    # The policy YAML sits beside the hooks and is configuration, not a hook.
    assert evidence["hooks"]["claude_hook_scripts"] == ["armed-gate.sh", "pre-push"]
    assert evidence["harness_present"] is True


def test_a_plugin_marketplace_shows_its_plugins_skills_and_agents():
    """A harness shipped as plugins lives one root per plugin.

    Audits of `xmartlabs/agent-skills` were shown 2 of its skills and none of
    its agents: the collector never looked under `plugins/<p>/skills/` or
    `plugins/<p>/agents/`, so five repeated audits said nothing about them.
    """
    files = {
        "pyproject.toml": "[project]\n",
        "CLAUDE.md": "# Rules\n",
        "plugins/tribunal/.claude-plugin/plugin.json": "{}\n",
        "plugins/tribunal/skills/review-pr/SKILL.md": "# Review a PR\n",
        "plugins/tribunal/agents/tribunal-refuter.md": "# Refuter\n",
        "plugins/homosapiens/skills/humanize/SKILL.md": "# Humanize\n",
        # A README beside the skills is documentation, not a skill.
        "plugins/tribunal/skills/README.md": "# Skills\n",
    }
    evidence = _evidence(files)

    assert sorted(evidence["skills"]) == [
        "plugins/homosapiens/skills/humanize/SKILL.md",
        "plugins/tribunal/skills/review-pr/SKILL.md",
    ]
    assert list(evidence["agents"]) == ["plugins/tribunal/agents/tribunal-refuter.md"]
    assert evidence["governance"]["skills"] == [
        "homosapiens:humanize",
        "tribunal:review-pr",
    ]
    assert evidence["governance"]["agents"] == ["tribunal:tribunal-refuter"]
    assert evidence["harness_present"] is True


def test_a_source_directory_named_skills_is_not_a_harness():
    """`skills/` counts only when it carries the marker that says what it is."""
    files = {
        "pyproject.toml": "[project]\n",
        "src/skills/__init__.py": "",
        "skills/router.py": "",
        "skills/README.md": "# Skills package\n",
    }
    evidence = _evidence(files)

    assert evidence["governance"]["skills"] == []
    assert evidence["harness_present"] is False


def test_an_agent_definition_is_found_however_deeply_it_is_nested():
    """Nesting says nothing about whether a file addresses an agent."""
    files = {
        "pyproject.toml": "[project]\n",
        "CLAUDE.md": "# Rules\n",
        ".claude/agents/review/deep.md": "# Deep reviewer\n",
    }
    evidence = _evidence(files)

    assert evidence["governance"]["agents"] == ["review/deep"]
    assert ".claude/agents/review/deep.md" in evidence["agents"]


def test_a_python_repository_with_nothing_for_an_agent_has_no_harness():
    files = {
        "pyproject.toml": "[project]\n",
        "src/app.py": "",
        ".github/workflows/ci.yml": "",
        ".pre-commit-config.yaml": "repos: []\n",
    }
    evidence = _evidence(files)

    # CI and pre-commit are engineering standards, not a harness: nothing here
    # was written for an agent.
    assert evidence["harness_present"] is False
    assert evidence["hooks"]["pre_commit"] is True
    assert evidence["next_step"] == svc.NEXT_STEP_NO_HARNESS
    assert "CLAUDE.md" in evidence["next_step_reason"]


def test_a_go_repository_stops_however_good_its_harness_is():
    files = {"go.mod": "module x\n", "CLAUDE.md": "# Rules\n", "main.go": ""}
    evidence = _evidence(files)

    assert evidence["harness_present"] is True
    assert evidence["runtime"]["unsupported"] == ["Go"]
    assert evidence["next_step"] == svc.NEXT_STEP_UNSUPPORTED
    assert "Go" in evidence["next_step_reason"]


def test_a_cobol_tree_with_no_marker_is_named_not_shrugged_at():
    files = {f"src/prog{i}.cbl": "" for i in range(5)}
    files["copybooks/acct.cpy"] = ""
    files["AGENTS.md"] = "# Agents\n"
    evidence = _evidence(files)

    assert evidence["runtime"]["ecosystems"] == ["COBOL"]
    assert evidence["runtime"]["dominant_language"] == "COBOL"
    assert evidence["next_step"] == svc.NEXT_STEP_UNSUPPORTED
    assert "COBOL" in evidence["next_step_reason"]


def test_python_files_without_a_project_marker_still_count_as_python():
    files = {"scripts/a.py": "", "scripts/b.py": "", "CLAUDE.md": "# R\n"}
    evidence = _evidence(files)

    assert evidence["runtime"]["supported"] == ["Python"]
    assert evidence["runtime"]["detected_from_files_only"] is True
    assert evidence["next_step"] == svc.NEXT_STEP_CONTINUE


def test_a_tree_with_nothing_executable_says_so():
    evidence = _evidence({"docs/index.md": "", "CLAUDE.md": "# R\n"})

    assert evidence["runtime"]["runner_can_execute"] is False
    assert evidence["next_step"] == svc.NEXT_STEP_UNSUPPORTED
    assert "No project marker" in evidence["next_step_reason"]


def test_the_language_census_ignores_vendored_trees():
    paths = ["node_modules/x/index.js"] * 50 + ["src/a.py", "src/b.py"]

    assert svc.language_census(paths) == [{"language": "Python", "files": 2}]


def test_the_runtime_gate_is_checked_before_the_harness_gate():
    """A repository the runner cannot build stops first, whatever it ships."""
    runtime = {
        "runner_can_execute": False,
        "unsupported": ["Rust"],
        "dominant_language": "Rust",
        "supported": [],
    }
    step, _ = svc.decide_next_step(runtime, present=True)
    assert step == svc.NEXT_STEP_UNSUPPORTED


def test_skills_shown_to_the_model_are_capped(monkeypatch):
    monkeypatch.setattr(svc, "_MAX_SKILLS", 2)
    files = {f".claude/skills/s{i}/SKILL.md": "x" for i in range(5)}
    files["pyproject.toml"] = ""
    evidence = _evidence(files)

    assert len(evidence["skills"]) == 2
    # The classification still counts them all — the cap is on what is read.
    assert len(evidence["governance"]["skills"]) == 5


def test_an_unpinned_target_cannot_be_audited():
    with pytest.raises(svc.RepositoryAuditError, match="not pinned"):
        svc.gather_evidence(_target(pinned_commit=""))


# --------------------------------------------------------------------------
# Findings — the model's answer, coerced, never trusted blindly
# --------------------------------------------------------------------------

GOOD_ANSWER = {
    "summary": "Este repositorio cuenta con un harness.",
    "harness_verdict": "partial",
    "harness_components": [
        {"kind": "rules_file", "name": "CLAUDE.md", "assessment": "Reglas claras."},
        {"kind": "spaceship", "name": "x", "assessment": "?"},
        {"kind": "skill", "assessment": "sin nombre"},
    ],
    "language_notes": "Python con pytest.",
    "standards": ["make test antes de terminar"],
    "gaps": [],
    "use_cases": [
        {"title": f"Caso {i}", "why": "porque sí", "validation_hint": "make test"}
        for i in range(10)
    ],
}


def test_a_fenced_json_answer_parses():
    raw = "```json\n" + json.dumps(GOOD_ANSWER) + "\n```"
    findings = svc.parse_findings(raw)

    assert "parse_error" not in findings
    assert findings["summary"].startswith("Este repositorio")
    assert findings["harness_verdict"] == "partial"


def test_an_unknown_component_kind_becomes_other_and_a_nameless_one_is_dropped():
    findings = svc.parse_findings(json.dumps(GOOD_ANSWER))

    kinds = [row["kind"] for row in findings["harness_components"]]
    assert kinds == ["rules_file", "other"]


def test_use_cases_are_capped_to_a_short_list():
    findings = svc.parse_findings(json.dumps(GOOD_ANSWER))

    assert len(findings["use_cases"]) == svc._MAX_USE_CASES


def test_a_verdict_outside_the_vocabulary_is_unknown():
    findings = svc.parse_findings(
        json.dumps({**GOOD_ANSWER, "harness_verdict": "great"})
    )

    assert findings["harness_verdict"] == "unknown"


def test_prose_instead_of_json_is_kept_as_a_parse_error_not_invented():
    findings = svc.parse_findings("El repo tiene un CLAUDE.md y dos skills.")

    assert set(findings) == {"parse_error"}


# --------------------------------------------------------------------------
# The report — fixed sections, the lab's verdict last and from the evidence
# --------------------------------------------------------------------------


def test_the_report_keeps_its_sections_in_order():
    evidence = _evidence(GOVERNED_PYTHON)
    findings = svc.parse_findings(json.dumps(GOOD_ANSWER))
    markdown = svc.render_markdown(findings, evidence, model="m")

    order = [
        markdown.index("## Resumen"),
        markdown.index("## Harness detectado — parcial"),
        markdown.index("## Lenguaje y runtime"),
        markdown.index("## Estándares de uso"),
        markdown.index("## Casos de uso posibles"),
        markdown.index("## Veredicto del laboratorio"),
    ]
    assert order == sorted(order)
    assert "**reglas** `CLAUDE.md`" in markdown
    assert "Se puede continuar" in markdown


def test_the_lab_verdict_comes_from_the_evidence_not_the_model():
    """A model calling a Go repository 'complete' does not make it runnable."""
    evidence = _evidence({"go.mod": "", "CLAUDE.md": "#"})
    findings = svc.parse_findings(
        json.dumps({**GOOD_ANSWER, "harness_verdict": "complete"})
    )
    markdown = svc.render_markdown(findings, evidence, model="m")

    assert "## Harness detectado — completo" in markdown
    assert "El runner puede ejecutarlo: **no**" in markdown
    assert "El laboratorio no puede ejecutarlo" in markdown


SWALLOWING = {
    **GOVERNED_PYTHON,
    "Makefile": "test:\n\tpytest || echo 'no tests'\n",
}


def test_the_evidence_carries_the_deterministic_checks():
    evidence = _evidence(SWALLOWING)
    by_id = {row["id"]: row for row in evidence["harness_checks"]}
    assert by_id["verify_target_exit_code"]["status"] == "fail"


def test_the_report_prints_the_checks_after_the_summary_and_before_the_model_reading():
    evidence = _evidence(SWALLOWING)
    findings = svc.parse_findings(json.dumps(GOOD_ANSWER))
    markdown = svc.render_markdown(findings, evidence, model="m")

    assert (
        markdown.index("## Resumen")
        < markdown.index("## Chequeos determinísticos")
        < markdown.index("## Harness detectado")
    )
    assert "❌ falla" in markdown
    assert "Qué hacer, en orden" in markdown


def test_the_checks_survive_a_model_that_answered_garbage():
    """The checks are the lab's: a parse error must not hide them."""
    evidence = _evidence(SWALLOWING)
    findings = svc.parse_findings("not json")
    markdown = svc.render_markdown(findings, evidence, model="m", raw="not json")
    assert "## Chequeos determinísticos" in markdown
    assert "Verification can fail" in markdown


def test_the_case_pass_is_shown_what_the_checks_flagged():
    evidence = _evidence(SWALLOWING)
    findings = svc.parse_findings(json.dumps(GOOD_ANSWER))
    prompt = svc.render_case_prompt(evidence, findings)
    flagged = json.loads(prompt.split("```json\n", 1)[1].rsplit("\n```", 1)[0])[
        "harness_checks_flagged"
    ]
    assert any(row["id"] == "verify_target_exit_code" for row in flagged)
    assert all(row["status"] in {"fail", "warn"} for row in flagged)


def test_a_parse_error_report_shows_what_the_model_said():
    evidence = _evidence(GOVERNED_PYTHON)
    findings = svc.parse_findings("not json")
    markdown = svc.render_markdown(findings, evidence, model="m", raw="not json")

    assert "no devolvió la estructura" in markdown
    assert "not json" in markdown
    assert "## Veredicto del laboratorio" in markdown


# --------------------------------------------------------------------------
# The task — one row per repository, failure written down
# --------------------------------------------------------------------------


def _seed(session, **over) -> TargetRepository:
    target = _target(**over)
    session.add(target)
    session.commit()
    return target


def test_the_task_stores_findings_markdown_and_evidence(session, monkeypatch):
    _seed(session)
    monkeypatch.setattr(
        svc, "gather_evidence", lambda t, **_kw: _evidence(GOVERNED_PYTHON, t)
    )
    seen: dict[str, str] = {}
    systems: list[str] = []

    def gateway(model: str, system: str, user: str) -> str:
        seen.update(model=model, system=system, user=user)
        systems.append(system)
        return json.dumps(GOOD_ANSWER)

    _audit_repository_body(session, "payments", "auditor-x", gateway=gateway)

    audit = svc.RepositoryAuditService(session).get("payments")
    assert audit is not None
    assert audit.status == "done"
    assert audit.auditor_model == "auditor-x"
    assert audit.commit == "a" * 40
    assert audit.findings_json["harness_verdict"] == "partial"
    assert audit.evidence_json["next_step"] == svc.NEXT_STEP_CONTINUE
    assert "## Veredicto del laboratorio" in audit.markdown
    # Two passes: the map first, the cases from the annotated map second.
    assert systems == [svc.COMPONENT_MAP_PROMPT, svc.CASE_PROPOSALS_PROMPT]
    assert "CLAUDE.md" in seen["user"]


def test_an_audit_is_filed_under_the_model_that_answered(session, monkeypatch):
    """The CLI answers a gateway name with its own default model.

    The audit used to be filed under the requested `gpt-5.4-mini` at 0.2 even
    when Sonnet read it at whatever temperature the CLI uses. The record now
    names what answered, keeps the request beside it, and lists every pass.
    """
    from harness_lab_app import llm_transport

    _seed(session)
    monkeypatch.setattr(
        svc, "gather_evidence", lambda t, **_kw: _evidence(GOVERNED_PYTHON, t)
    )

    def chat_reply(model, system, user, **_kw):
        return llm_transport.ChatReply(
            text=json.dumps(GOOD_ANSWER),
            transport="claude_cli",
            requested_model=model,
            model="claude-sonnet-5-5",
            requested_temperature=0.2,
            temperature=None,
        )

    monkeypatch.setattr(llm_transport, "chat_reply", chat_reply)

    _audit_repository_body(session, "payments", "gpt-5.4-mini")

    audit = svc.RepositoryAuditService(session).get("payments")
    assert audit.status == "done"
    assert audit.auditor_model == "claude-sonnet-5-5 (requested gpt-5.4-mini)"
    passes = audit.evidence_json["llm"]
    assert len(passes) == 2
    assert {p["effective_model"] for p in passes} == {"claude-sonnet-5-5"}
    assert {p["temperature"] for p in passes} == {None}
    assert {p["requested_temperature"] for p in passes} == {0.2}


def test_an_audit_served_as_requested_keeps_the_plain_model_name(session, monkeypatch):
    from harness_lab_app import llm_transport

    _seed(session)
    monkeypatch.setattr(
        svc, "gather_evidence", lambda t, **_kw: _evidence(GOVERNED_PYTHON, t)
    )
    monkeypatch.setattr(
        llm_transport,
        "chat_reply",
        lambda model, *_a, **_kw: llm_transport.ChatReply(
            text=json.dumps(GOOD_ANSWER),
            transport="gateway",
            requested_model=model,
            model=model,
            requested_temperature=0.2,
            temperature=0.2,
        ),
    )

    _audit_repository_body(session, "payments", "gpt-5.4-mini")

    audit = svc.RepositoryAuditService(session).get("payments")
    assert audit.auditor_model == "gpt-5.4-mini"


def test_an_audit_served_by_an_unreported_model_says_unknown(session, monkeypatch):
    """A CLI too old to report usage, sent a name it cannot serve.

    Filing that under the requested `gpt-5.4-mini` names the one model the
    CLI certainly did not use.
    """
    from harness_lab_app import llm_transport

    _seed(session)
    monkeypatch.setattr(
        svc, "gather_evidence", lambda t, **_kw: _evidence(GOVERNED_PYTHON, t)
    )
    monkeypatch.setattr(
        llm_transport,
        "chat_reply",
        lambda model, *_a, **_kw: llm_transport.ChatReply(
            text=json.dumps(GOOD_ANSWER),
            transport="claude_cli",
            requested_model=model,
            model="",
            requested_temperature=0.2,
            temperature=None,
        ),
    )

    _audit_repository_body(session, "payments", "gpt-5.4-mini")

    audit = svc.RepositoryAuditService(session).get("payments")
    assert audit.auditor_model == "unknown (requested gpt-5.4-mini)"


def test_a_failing_auditor_leaves_a_failed_row_not_a_running_one(session, monkeypatch):
    _seed(session)
    monkeypatch.setattr(
        svc, "gather_evidence", lambda t, **_kw: _evidence(GOVERNED_PYTHON, t)
    )

    def gateway(*_: str) -> str:
        raise RuntimeError("gateway down")

    _audit_repository_body(session, "payments", gateway=gateway)

    audit = svc.RepositoryAuditService(session).get("payments")
    assert audit.status == "failed"
    assert "gateway down" in (audit.error or "")


def test_an_unpinned_repository_fails_with_the_reason(session):
    _seed(session, pinned_commit="")

    _audit_repository_body(session, "payments", gateway=lambda *_: "{}")

    audit = svc.RepositoryAuditService(session).get("payments")
    assert audit.status == "failed"
    assert "not pinned" in (audit.error or "")


def test_a_re_audit_replaces_the_row(session, monkeypatch):
    _seed(session)
    monkeypatch.setattr(
        svc, "gather_evidence", lambda t, **_kw: _evidence(GOVERNED_PYTHON, t)
    )
    _audit_repository_body(
        session, "payments", gateway=lambda *_: json.dumps(GOOD_ANSWER)
    )
    _audit_repository_body(session, "payments", gateway=lambda *_: "prose")

    rows = session.exec(svc.select(svc.RepositoryAudit)).all()
    assert len(rows) == 1
    assert rows[0].findings_json.get("parse_error")


# --------------------------------------------------------------------------
# The endpoints — pinned or refused, queued or told why not
# --------------------------------------------------------------------------


def _route(app, path: str, method: str):
    for route in app.router.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


def test_the_post_refuses_an_unpinned_repository(session):
    _seed(session, pinned_commit="")
    post = _route(create_app(), "/api/targets/{target_id}/repository-audit", "POST")

    with pytest.raises(HTTPException) as exc:
        post("payments", None, session)
    assert exc.value.status_code == 409
    assert "Pin" in exc.value.detail


def test_the_post_queues_and_marks_the_row_running(session, monkeypatch):
    _seed(session)
    queued: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        api_module, "enqueue_repository_audit", lambda t, m: queued.append((t, m))
    )
    app = create_app()
    post = _route(app, "/api/targets/{target_id}/repository-audit", "POST")
    get = _route(app, "/api/targets/{target_id}/repository-audit", "GET")

    assert post("payments", "m", session) == {
        "status": "queued",
        "target_id": "payments",
    }
    assert queued == [("payments", "m")]
    record = get("payments", session)
    assert record.status == "running"
    assert record.commit == "a" * 40


def test_a_dead_queue_is_a_503_and_a_failed_row(session, monkeypatch):
    _seed(session)

    def boom(*_):
        raise ConnectionError("broker down")

    monkeypatch.setattr(api_module, "enqueue_repository_audit", boom)
    post = _route(create_app(), "/api/targets/{target_id}/repository-audit", "POST")

    with pytest.raises(HTTPException) as exc:
        post("payments", None, session)
    assert exc.value.status_code == 503
    assert svc.RepositoryAuditService(session).get("payments").status == "failed"


def test_the_get_is_a_404_until_an_audit_exists(session):
    _seed(session)
    get = _route(create_app(), "/api/targets/{target_id}/repository-audit", "GET")

    with pytest.raises(HTTPException) as exc:
        get("payments", session)
    assert exc.value.status_code == 404


def test_the_post_is_a_404_for_an_unknown_repository(session):
    post = _route(create_app(), "/api/targets/{target_id}/repository-audit", "POST")

    with pytest.raises(HTTPException) as exc:
        post("nope", None, session)
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------
# The real thing — a git checkout under data/checkouts, read at its pin
# --------------------------------------------------------------------------


def _git(cwd, *args: str) -> str:
    import subprocess

    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        },
    )
    return result.stdout.strip()


def test_gather_evidence_reads_the_pinned_commit_of_a_real_checkout(
    monkeypatch, tmp_path
):
    """The tree the model sees is the pin, not whatever HEAD moved on to."""
    from harness_lab_app import checkout_service, config

    settings = config.Settings(repo_root=tmp_path)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(checkout_service, "get_settings", lambda: settings)
    repo = settings.checkouts_dir / "payments"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    for name, body in GOVERNED_PYTHON.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "governed")
    pinned = _git(repo, "rev-parse", "HEAD")
    # HEAD moves on: the harness is removed after the pin.
    (repo / "CLAUDE.md").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "drop the rules")

    target = _target(checkout_path=str(repo), pinned_commit=pinned)
    evidence = svc.gather_evidence(target)

    assert evidence["commit"] == pinned
    assert evidence["rules_files"]["CLAUDE.md"].startswith("# Rules")
    assert evidence["hooks"]["claude_settings_events"] == ["SessionStart", "Stop"]
    assert evidence["next_step"] == svc.NEXT_STEP_CONTINUE
