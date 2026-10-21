"""`make demo-check`: a read-only readiness checklist before showing the lab."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import demo_check  # noqa: E402


def test_a_gateway_with_a_real_token_is_a_credential():
    env = {"GATEWAY_BASE_URL": "http://localhost:4000/v1", "GATEWAY_TOKEN": "sk-real"}
    row = demo_check.credential_check(env)
    assert row["ok"] is True
    assert "gateway" in row["detail"]


def test_the_placeholder_gateway_token_is_not_a_credential():
    env = {"GATEWAY_BASE_URL": "http://x/v1", "GATEWAY_TOKEN": "sk-local-change-me"}
    assert demo_check.credential_check(env)["ok"] is False


def test_a_claude_credential_alone_is_enough():
    row = demo_check.credential_check({"CLAUDE_CODE_OAUTH_TOKEN": "tok"})
    assert row["ok"] is True
    assert "Claude" in row["detail"]


def test_the_credential_check_never_prints_a_value():
    secret = "sk-ant-very-secret-value"
    row = demo_check.credential_check({"ANTHROPIC_API_KEY": secret})
    assert secret not in row["detail"]
    assert "ANTHROPIC_API_KEY" in row["detail"]


def test_no_credential_says_where_to_look():
    row = demo_check.credential_check({})
    assert row["ok"] is False
    assert "onboarding" in row["fix"].lower()


def test_read_env_file_ignores_comments_and_blank_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n\nGATEWAY_TOKEN=abc\nexport X=1\nBAD LINE\n")
    assert demo_check.read_env_file(env_file) == {"GATEWAY_TOKEN": "abc", "X": "1"}


def test_a_missing_env_file_reads_as_empty(tmp_path):
    assert demo_check.read_env_file(tmp_path / "nope") == {}


def test_the_api_check_reports_cases_when_the_stack_is_up():
    responses = {
        "/api/health": {"status": "ok"},
        "/api/cases": [{"case_id": "tetris-v2"}, {"case_id": "cli-textstats-v1"}],
    }
    rows = demo_check.api_checks(
        "http://api", fetch=lambda url: responses[url[len("http://api") :]]
    )
    by_name = {row["name"]: row for row in rows}
    assert by_name["Lab API"]["ok"] is True
    assert by_name["Cases loaded"]["ok"] is True
    assert "2" in by_name["Cases loaded"]["detail"]


def test_the_api_check_says_how_to_start_the_stack_when_it_is_down():
    def fetch(url):
        raise OSError("connection refused")

    rows = demo_check.api_checks("http://api", fetch=fetch)
    assert rows[0]["ok"] is False
    assert "make app-up" in rows[0]["fix"]


def test_exit_code_is_nonzero_when_any_required_check_fails():
    rows = [
        {"name": "a", "ok": True, "detail": "", "fix": "", "required": True},
        {"name": "b", "ok": False, "detail": "", "fix": "x", "required": False},
    ]
    assert demo_check.exit_code(rows) == 0
    rows[1]["required"] = True
    assert demo_check.exit_code(rows) == 1
