"""Deterministic harness checks (ADR-0065).

Every check reads paths and file text only — no model, no network, no process —
so each one is argued with here against a few strings.
"""

from __future__ import annotations

import json

import pytest

from harness_lab_app.harness_checks import (
    FAIL,
    NOT_APPLICABLE,
    PASS,
    WARN,
    run_checks,
    summarize_checks,
)


def _tree(files: dict[str, str]):
    paths = sorted(files)
    return paths, lambda path: files.get(path, "")


def _check(files: dict[str, str], check_id: str) -> dict:
    paths, read = _tree(files)
    by_id = {row["id"]: row for row in run_checks(paths, read)}
    return by_id[check_id]


# --- the verification target -------------------------------------------------

SWALLOWING_MAKEFILE = (
    "test:\n"
    "\t@make format\n"
    '\t@pytest tests/ --cov=src || echo "No tests found to run"\n'
    "\t@echo done\n"
)


def test_a_test_target_that_swallows_failures_fails():
    row = _check({"Makefile": SWALLOWING_MAKEFILE}, "verify_target_exit_code")
    assert row["status"] == FAIL
    assert "|| echo" in row["evidence"]
    assert row["fix"]


def test_a_test_target_that_propagates_failures_passes():
    makefile = "test:\n\tpytest tests/ -q\n"
    assert _check({"Makefile": makefile}, "verify_target_exit_code")["status"] == PASS


def test_a_test_target_with_or_true_also_swallows():
    makefile = "check:\n\tnpm test || true\n"
    assert _check({"Makefile": makefile}, "verify_target_exit_code")["status"] == FAIL


@pytest.mark.parametrize(
    "line",
    [
        "@command -v uv >/dev/null || echo 'install uv first'",
        "@test -d .venv || echo 'no venv'",
        "@[ -f .env ] || true",
    ],
)
def test_a_fallback_on_a_precondition_is_not_a_swallowed_test(line):
    """Only a fallback on the verifying command itself hides a failing test."""
    makefile = f"test:\n\t{line}\n\tpytest -q\n"
    assert _check({"Makefile": makefile}, "verify_target_exit_code")["status"] == PASS


def test_a_multi_target_rule_counts_for_every_target():
    makefile = "lint test:\n\tpytest || true\n"
    assert _check({"Makefile": makefile}, "verify_target_exit_code")["status"] == FAIL


def test_a_target_is_judged_with_the_recipes_of_its_prerequisites():
    makefile = "check: format lint\n\npytest-ok:\n\nformat:\n\truff format .\nlint:\n\truff check .\n"
    assert _check({"Makefile": makefile}, "verify_target_read_only")["status"] == WARN


def test_package_json_test_script_that_swallows_fails():
    package = json.dumps({"scripts": {"test": "vitest run || exit 0"}})
    row = _check({"package.json": package}, "verify_target_exit_code")
    assert row["status"] == FAIL


def test_no_verification_target_is_a_warning_not_a_pass():
    row = _check({"README.md": "hi"}, "verify_target_exit_code")
    assert row["status"] == WARN


def test_a_test_target_that_reformats_the_tree_is_reported():
    row = _check({"Makefile": SWALLOWING_MAKEFILE}, "verify_target_read_only")
    assert row["status"] == WARN
    assert "format" in row["evidence"]


def test_a_read_only_test_target_passes():
    makefile = "test:\n\tpytest -q\n"
    assert _check({"Makefile": makefile}, "verify_target_read_only")["status"] == PASS


# --- hooks -------------------------------------------------------------------


def _settings(hooks: dict) -> str:
    return json.dumps({"hooks": hooks})


NUDGE = (
    "#!/usr/bin/env bash\n"
    "changed=$(git status --porcelain -- src)\n"
    "cat <<'EOF'\nReminder: run make check\nEOF\n"
    "exit 0\n"
)


def test_a_stop_hook_that_only_prints_is_advisory():
    files = {
        ".claude/settings.json": _settings(
            {
                "Stop": [
                    {
                        "hooks": [
                            {"type": "command", "command": "bash .claude/hooks/stop.sh"}
                        ]
                    }
                ]
            }
        ),
        ".claude/hooks/stop.sh": NUDGE,
    }
    row = _check(files, "hooks_can_block")
    assert row["status"] == WARN
    assert "stop.sh" in row["evidence"]


def test_a_hook_that_emits_a_decision_can_block():
    script = (
        "#!/usr/bin/env bash\n"
        'echo \'{"decision": "block", "reason": "run the tests"}\'\n'
    )
    files = {
        ".claude/settings.json": _settings(
            {
                "Stop": [
                    {
                        "hooks": [
                            {"type": "command", "command": "bash .claude/hooks/gate.sh"}
                        ]
                    }
                ]
            }
        ),
        ".claude/hooks/gate.sh": script,
    }
    assert _check(files, "hooks_can_block")["status"] == PASS


def test_a_hook_that_exits_2_can_block():
    script = "#!/usr/bin/env bash\nmake check || exit 2\n"
    files = {
        ".claude/settings.json": _settings(
            {
                "PreToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": "./.claude/hooks/pre.sh"}
                        ]
                    }
                ]
            }
        ),
        ".claude/hooks/pre.sh": script,
    }
    assert _check(files, "hooks_can_block")["status"] == PASS


def test_no_hooks_at_all_is_not_applicable():
    assert _check({"CLAUDE.md": "x"}, "hooks_can_block")["status"] == NOT_APPLICABLE


# --- instruction budget -----------------------------------------------------


def test_a_short_rules_file_passes_the_budget():
    files = {"CLAUDE.md": "# Rules\n" + "- keep it short\n" * 40}
    row = _check(files, "instruction_budget")
    assert row["status"] == PASS
    assert row["details"]["files"]["CLAUDE.md"]["lines"] == 41


def test_a_long_rules_file_is_a_warning():
    files = {"AGENTS.md": "- rule\n" * 260}
    assert _check(files, "instruction_budget")["status"] == WARN


def test_no_rules_file_fails_the_budget_check():
    assert _check({"src/a.py": "x"}, "instruction_budget")["status"] == FAIL


# --- skills -----------------------------------------------------------------


def test_skills_with_name_and_description_pass():
    skill = "---\nname: verify\ndescription: Use before calling work done.\n---\nBody\n"
    files = {".claude/skills/verify/SKILL.md": skill}
    assert _check(files, "skill_frontmatter")["status"] == PASS


def test_a_skill_without_description_fails():
    files = {".claude/skills/verify/SKILL.md": "---\nname: verify\n---\nBody\n"}
    row = _check(files, "skill_frontmatter")
    assert row["status"] == FAIL
    assert "verify" in row["evidence"]


def test_a_skill_name_with_underscores_is_a_warning():
    skill = "---\nname: verify_changes\ndescription: Use before done.\n---\n"
    files = {".claude/skills/verify_changes/SKILL.md": skill}
    assert _check(files, "skill_frontmatter")["status"] == WARN


def test_projected_copies_of_one_skill_count_once():
    """ml-python-base projects each governed skill into .claude/, .agents/ and
    .codex/. Those copies are symlinks; read through git they come back as the
    link target path, not as a SKILL.md — they must not count as broken skills."""
    good = "---\nname: verify\ndescription: Use before done.\n---\n"
    files = {
        ".github/skills/verify.md": good,
        ".claude/skills/verify/SKILL.md": "../../../.github/skills/verify.md",
        ".agents/skills/verify/SKILL.md": "../../../.github/skills/verify.md",
    }
    row = _check(files, "skill_frontmatter")
    assert row["status"] == PASS
    assert row["details"]["skills"] == 1


def test_plugin_skills_are_found():
    skill = "---\nname: pr-evidence\ndescription: Use when a PR changes UI.\n---\n"
    files = {"plugins/pr-kit/skills/pr-evidence/SKILL.md": skill}
    row = _check(files, "skill_frontmatter")
    assert row["status"] == PASS
    assert row["details"]["skills"] == 1


def test_the_test_target_is_reported_before_lint():
    """When several targets swallow failures, `test`/`check` lead the evidence:
    they are the ones the agent is told to run."""
    makefile = (
        "lint:\n\truff check src || true\n\tbandit -r src || true\n\tmypy src || true\n"
        "test:\n\tpytest || echo none\n"
    )
    row = _check({"Makefile": makefile}, "verify_target_exit_code")
    assert row["evidence"].startswith("`Makefile:test`")


def test_no_skills_is_not_applicable():
    assert _check({"CLAUDE.md": "x"}, "skill_frontmatter")["status"] == NOT_APPLICABLE


# --- MCP pinning ------------------------------------------------------------


def test_an_mcp_server_on_latest_is_a_warning():
    mcp = json.dumps(
        {
            "mcpServers": {
                "docs": {
                    "command": "npx",
                    "args": ["-y", "@upstash/context7-mcp@latest"],
                }
            }
        }
    )
    row = _check({".mcp.json": mcp}, "mcp_pinned")
    assert row["status"] == WARN
    assert "context7" in row["evidence"]


@pytest.mark.parametrize(
    "spec, culprit",
    [
        (
            {"command": "uvx", "args": ["mcp-server-git", "--repository", "."]},
            "mcp-server-git",
        ),
        ({"command": "npx", "args": ["-y", "mcp-remote@latest"]}, "mcp-remote"),
        (
            {
                "command": "sh",
                "args": ["-c", "exec npx -y @notionhq/notion-mcp-server"],
            },
            "notion",
        ),
        ({"command": "pipx", "args": ["run", "some-mcp"]}, "some-mcp"),
    ],
)
def test_unpinned_servers_are_found_whatever_the_launcher(spec, culprit):
    row = _check({".mcp.json": json.dumps({"mcpServers": {"s": spec}})}, "mcp_pinned")
    assert row["status"] == WARN
    assert culprit in row["evidence"]


def test_pinned_launchers_and_url_servers_pass_and_are_counted_apart():
    servers = {
        "git": {
            "command": "uvx",
            "args": ["mcp-server-git==2025.1.14", "--repository", "."],
        },
        "docs": {"command": "npx", "args": ["-y", "@upstash/context7-mcp@1.0.14"]},
        "remote": {"type": "http", "url": "https://mcp.example.com"},
    }
    row = _check({".mcp.json": json.dumps({"mcpServers": servers})}, "mcp_pinned")
    assert row["status"] == PASS
    assert "2 package server(s) pinned" in row["evidence"]
    assert "1 remote" in row["evidence"]


def test_pinned_mcp_servers_pass():
    mcp = json.dumps(
        {
            "mcpServers": {
                "docs": {
                    "command": "npx",
                    "args": ["-y", "@upstash/context7-mcp@1.0.14"],
                }
            }
        }
    )
    assert _check({".mcp.json": mcp}, "mcp_pinned")["status"] == PASS


# --- CI and harness version ---------------------------------------------------


def test_ci_that_runs_the_gate_passes():
    workflow = "jobs:\n  gate:\n    steps:\n      - run: make check\n"
    files = {".github/workflows/ci.yml": workflow, "Makefile": "check:\n\tpytest\n"}
    assert _check(files, "ci_runs_gate")["status"] == PASS


def test_no_ci_is_a_warning():
    assert _check({"Makefile": "check:\n\tpytest\n"}, "ci_runs_gate")["status"] == WARN


def test_a_declared_harness_version_passes():
    files = {".harness-version.yaml": "ref: v1.0.0\n"}
    row = _check(files, "harness_version_declared")
    assert row["status"] == PASS
    assert "v1.0.0" in row["evidence"]


def test_an_undeclared_harness_version_is_a_warning():
    assert _check({"CLAUDE.md": "x"}, "harness_version_declared")["status"] == WARN


# --- summary ----------------------------------------------------------------


def test_summary_counts_and_orders_failures_first():
    paths, read = _tree({"Makefile": SWALLOWING_MAKEFILE})
    checks = run_checks(paths, read)
    summary = summarize_checks(checks)
    assert summary["fail"] >= 1
    assert sum(summary[k] for k in (PASS, WARN, FAIL, NOT_APPLICABLE)) == len(checks)
    statuses = [row["status"] for row in summary["ordered"]]
    assert statuses.index(FAIL) < statuses.index(WARN)


def test_every_check_has_the_same_shape():
    paths, read = _tree({"Makefile": SWALLOWING_MAKEFILE, "CLAUDE.md": "x"})
    for row in run_checks(paths, read):
        assert set(row) >= {"id", "title", "status", "evidence", "fix", "why"}
        assert row["status"] in {PASS, WARN, FAIL, NOT_APPLICABLE}


def test_a_hostile_hook_command_is_checked_in_linear_time():
    """The command comes from the audited repository. A long run of path-like
    characters with no script suffix used to backtrack quadratically and hang
    the audit worker."""
    import time

    hostile = "a" * 60_000
    files = {
        ".claude/settings.json": _settings(
            {"Stop": [{"hooks": [{"type": "command", "command": hostile}]}]}
        )
    }
    start = time.perf_counter()
    _check(files, "hooks_can_block")
    assert time.perf_counter() - start < 1.0


def test_hook_commands_are_capped_and_kept_on_one_line_in_the_evidence():
    """Evidence reaches the model prompt and the Markdown report; a newline in a
    command could inject report structure, and its length is the repo's choice."""
    command = "echo hi\n## Injected heading\n" + "x" * 5_000
    files = {
        ".claude/settings.json": _settings(
            {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}
        )
    }
    row = _check(files, "hooks_can_block")
    assert "\n" not in row["evidence"]
    assert len(row["evidence"]) < 600
    assert all("\n" not in label for label in row["details"]["advisory"])


@pytest.mark.parametrize("bad", ["{not json", "[]", '{"hooks": "x"}'])
def test_malformed_settings_never_raise(bad):
    row = _check({".claude/settings.json": bad}, "hooks_can_block")
    assert row["status"] in {NOT_APPLICABLE, WARN}


def test_a_cursorrules_only_repo_has_a_rules_file():
    assert (
        _check({".cursorrules": "- be brief\n"}, "instruction_budget")["status"] == PASS
    )


def test_the_budget_fix_names_the_limit_that_was_crossed():
    row = _check({"AGENTS.md": ("x" * 200 + "\n") * 60}, "instruction_budget")
    assert row["status"] == WARN
    assert "tokens" in row["fix"] and "lines" not in row["fix"].split(":")[0]
