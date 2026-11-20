"""Connecting a native CLI from the UI (System → Agent CLIs, ADR-0094).

The device-login fixture is what codex-cli 0.171.0 printed inside the runner
image (code replaced). The test fixtures are the providers' own failure
messages, verbatim: a test that only checked for a credential file would have
called Gemini "connected" while Google refused every request.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from harness_lab_app import native_connect

CODEX_DEVICE_OUTPUT = (
    "\x1b[0mWelcome to Codex [v\x1b[90m0.171.0\x1b[0m]\n"
    "Follow these steps to sign in with ChatGPT using device code authorization:\n\n"
    "1. Open this link in your browser and sign in to your account\n"
    "   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n\n"
    "2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n"
    "   \x1b[94mABCD-12345\x1b[0m\n"
)


def test_the_device_url_and_code_are_read_through_the_colour_codes() -> None:
    assert native_connect.parse_device_prompt(CODEX_DEVICE_OUTPUT) == {
        "url": "https://auth.openai.com/codex/device",
        "code": "ABCD-12345",
    }


def test_nothing_is_published_before_both_are_printed() -> None:
    first_half = CODEX_DEVICE_OUTPUT.split("2. Enter")[0]
    assert native_connect.parse_device_prompt(first_half) is None


def test_a_login_in_flight_is_not_restarted() -> None:
    now = time.time()
    waiting = {"state": "waiting", "expires_at": now + 600, "updated_at": now}
    expired = {"state": "waiting", "expires_at": now - 1, "updated_at": now - 900}
    stale_start = {"state": "starting", "updated_at": now - 600}

    assert native_connect.login_in_flight(waiting, now)
    assert not native_connect.login_in_flight(expired, now)
    assert not native_connect.login_in_flight(stale_start, now)
    assert not native_connect.login_in_flight({"state": "connected"}, now)


def test_state_records_round_trip_and_default_to_idle(tmp_path: Path) -> None:
    assert native_connect.read_state("codex", "login", tmp_path)["state"] == "idle"

    native_connect.write_state(
        "codex", "login", {"state": "waiting", "code": "X"}, tmp_path
    )
    record = native_connect.read_state("codex", "login", tmp_path)

    assert record["state"] == "waiting"
    assert record["harness"] == "codex"
    assert oct(tmp_path.stat().st_mode & 0o777) == "0o700"


def test_a_codex_answer_is_a_passed_test() -> None:
    output = "\n".join(
        json.dumps(e)
        for e in (
            {"type": "thread.started", "thread_id": "t"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "i", "type": "agent_message", "text": "ok"},
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 9, "output_tokens": 1},
            },
        )
    )
    result = native_connect.summarize_test("codex", output, 0)

    assert result["state"] == "ok"
    assert result["answer"] == "ok"


@pytest.mark.parametrize(
    ("harness", "output", "exit_code", "words"),
    [
        (
            "codex",
            json.dumps(
                {
                    "type": "turn.failed",
                    "error": {"message": "unexpected status 401 Unauthorized"},
                }
            ),
            1,
            "401 Unauthorized",
        ),
        (
            "gemini",
            "An unexpected critical error occurred: This client is no longer "
            "supported for Gemini Code Assist for individuals.",
            0,
            "no longer supported",
        ),
        (
            "gemini",
            "An unexpected critical error occurred: Resource has been exhausted "
            "(e.g. check quota). (Status: 429)",
            0,
            "exhausted",
        ),
    ],
)
def test_a_providers_refusal_is_a_failed_test_in_its_own_words(
    harness: str, output: str, exit_code: int, words: str
) -> None:
    # Gemini printed these and exited 0 with no event at all: an exit code
    # alone would have called the plan connected.
    result = native_connect.summarize_test(harness, output, exit_code)

    assert result["state"] == "failed"
    assert words in result["error"]


def test_gemini_is_marked_unavailable_and_codex_connectable() -> None:
    assert "2026-06-18" in native_connect.UNAVAILABLE["gemini"]
    assert native_connect.CONNECTABLE["codex"]["login"] == [
        "codex",
        "login",
        "--device-auth",
    ]


def test_the_connection_test_uses_a_runs_flags_and_one_prompt() -> None:
    cmd = native_connect.test_command("codex", "gpt-6.2-sol")

    assert cmd[:2] == ["codex", "exec"]
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert cmd[-1] == native_connect.TEST_PROMPT
    assert cmd[cmd.index("-m") + 1] == "gpt-6.2-sol"


# --- API (called through the router, as the other API tests do) -------------


def _endpoint(app, path: str, method: str):
    for route in app.router.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from harness_lab_app import celery_app
    from harness_lab_app.api import create_app

    sent: list[tuple] = []
    monkeypatch.setattr(native_connect, "state_dir", lambda _root=None: tmp_path)
    monkeypatch.setattr(
        celery_app,
        "enqueue_native_cli",
        lambda task, harness, **kw: sent.append((task, harness, kw)),
    )
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    app = create_app()
    return app, sent


def test_connecting_codex_queues_the_login_once(api) -> None:
    app, sent = api
    connect = _endpoint(app, "/api/harness/clis/{harness}/connect", "POST")
    read = _endpoint(app, "/api/harness/clis/{harness}/connect", "GET")

    first = connect("codex")
    native_connect.write_state(
        "codex",
        "login",
        {"state": "waiting", "url": "u", "code": "c", "expires_at": time.time() + 600},
    )
    second = connect("codex")

    assert first["state"] == "queued"
    assert second["state"] == "waiting"  # not restarted: one code at a time
    assert sent == [("connect_native_cli", "codex", {})]
    assert read("codex")["code"] == "c"


def test_gemini_cannot_be_connected_and_says_why(api) -> None:
    from fastapi import HTTPException

    app, sent = api
    connect = _endpoint(app, "/api/harness/clis/{harness}/connect", "POST")
    with pytest.raises(HTTPException) as exc:
        connect("gemini")

    assert exc.value.status_code == 409
    assert "Antigravity" in exc.value.detail
    assert sent == []


def test_testing_a_cli_queues_one_request(api) -> None:
    app, sent = api
    test = _endpoint(app, "/api/harness/clis/{harness}/test", "POST")

    assert test("codex", "gpt-6.2-sol")["state"] == "queued"
    assert sent == [("test_native_cli", "codex", {"model": "gpt-6.2-sol"})]


def test_disconnect_is_done_by_the_worker(api) -> None:
    app, sent = api
    disconnect = _endpoint(app, "/api/harness/clis/{harness}/connect", "DELETE")

    disconnect("codex")

    assert sent == [("disconnect_native_cli", "codex", {})]


def test_an_unknown_cli_is_404(api) -> None:
    from fastapi import HTTPException

    app, _sent = api
    for path in (
        "/api/harness/clis/{harness}/connect",
        "/api/harness/clis/{harness}/test",
    ):
        with pytest.raises(HTTPException) as exc:
            _endpoint(app, path, "POST")("opencode")
        assert exc.value.status_code == 404


def test_the_native_cli_tasks_go_to_the_interactive_queue(monkeypatch) -> None:
    from harness_lab_app import celery_app

    calls: list[dict] = []
    monkeypatch.setattr(
        celery_app.celery_app,
        "send_task",
        lambda name, **kw: calls.append({"name": name, **kw}),
    )

    celery_app.enqueue_native_cli("connect_native_cli", "codex")

    assert calls[0]["queue"] == celery_app.INTERACTIVE_QUEUE
    with pytest.raises(ValueError):
        celery_app.enqueue_native_cli("build_runner_image", "codex")
