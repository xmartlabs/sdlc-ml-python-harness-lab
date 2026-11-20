"""Connecting a native CLI from the UI (System → Agent CLIs, ADR-0094).

The device-login fixture is what codex-cli 0.171.0 printed inside the runner
image (code replaced). The test fixtures are the providers' own failure
messages, verbatim: a test that only checked for a credential file would have
called Gemini "connected" while Google refused every request. The login
failure lines are the ones in the codex 0.171.0 binary.

The login regression these pin (2026-11-20): a worker that died after
publishing the code left the record on ``waiting`` for hours, and the card kept
showing that dead code as "expires in 0 min" with no way to ask for another.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any

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


# --- the code's lifetime, as the API shows it --------------------------------


def test_a_waiting_record_the_worker_never_closed_reads_expired() -> None:
    # The record found on disk after the 2026-11-20 test: "waiting", its code
    # six hours past expiry, because the worker died before writing an end.
    now = 1_795_205_698.0
    stuck = {
        "state": "waiting",
        "url": "https://auth.openai.com/codex/device",
        "code": "ABCD-12345",
        "expires_at": now - 21_626,
        "updated_at": now - 22_526,
    }

    view = native_connect.login_view(stuck, now)

    assert view["state"] == "expired"
    assert "code" not in view  # a dead code is never shown as one to enter
    assert view["settling"] is False


def test_an_expired_code_settles_only_briefly_while_the_worker_may_answer() -> None:
    expires = 1_000.0
    record = {
        "state": "waiting",
        "code": "ABCD-12345",
        "expires_at": expires,
        "worker_deadline": expires + 300,
    }

    assert native_connect.login_view(record, expires + 5)["settling"] is True
    assert (
        native_connect.login_view(record, expires + native_connect.SETTLE_SECONDS)[
            "settling"
        ]
        is False
    )


def test_the_countdown_reference_is_the_servers_clock() -> None:
    # A browser whose clock is ten minutes fast computes the time left from
    # `expires_at - server_time`, never from its own `Date.now()`: the full
    # fifteen minutes show right after the code is issued, not "0 min".
    issued = 1_795_183_161.5
    record = {"state": "waiting", "code": "X", "expires_at": issued + 900}

    view = native_connect.login_view(record, issued + 0.4)

    assert view["state"] == "waiting"
    assert view["expires_at"] - view["server_time"] == pytest.approx(899.6)


def test_a_login_no_worker_picked_up_is_a_timeout_not_a_spinner() -> None:
    now = 10_000.0
    queued = {
        "state": "queued",
        "updated_at": now - native_connect.START_TIMEOUT_SECONDS,
    }
    starting = {"state": "starting", "updated_at": now - 30, "worker_deadline": now - 1}

    assert native_connect.login_view(queued, now)["reason"] == "timeout"
    assert "interactive worker" in native_connect.login_view(queued, now)["error"]
    assert native_connect.login_view(starting, now)["state"] == "failed"
    assert (
        native_connect.login_view({**starting, "worker_deadline": now + 60}, now)[
            "state"
        ]
        == "starting"
    )


def test_the_lifetime_is_the_one_the_cli_printed() -> None:
    assert native_connect.printed_lifetime(CODEX_DEVICE_OUTPUT) == 15 * 60
    assert native_connect.printed_lifetime("no lifetime here") is None
    spec = native_connect.CONNECTABLE["codex"]
    # The worker's budget outlives the code; the code's countdown does not.
    assert spec["code_lifetime_seconds"] == 15 * 60
    assert 5 * 60 <= spec["grace_seconds"] <= 10 * 60


# --- how a login ends ---------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "exit_code", "stop_reason", "state", "reason"),
    [
        ("Error logging in: device code has expired.", 1, None, "expired", None),
        ("device auth timed out after 15 minutes", 1, None, "expired", None),
        (
            "Error logging in: device auth failed with status 403",
            1,
            None,
            "failed",
            "rejected",
        ),
        ("access_denied", 1, None, "failed", "rejected"),
        (
            "device code login is not enabled for this Codex server. Use the "
            "browser login or verify the server URL.",
            1,
            None,
            "failed",
            "rejected",
        ),
        (
            "Error logging in: error sending request for url "
            "(https://auth.openai.com/api/accounts/deviceauth/usercode)",
            1,
            None,
            "failed",
            "connection",
        ),
        ("", -1, "no_code", "failed", "timeout"),
        (CODEX_DEVICE_OUTPUT, 137, "deadline", "expired", None),
        ("panic: something else", 101, None, "failed", "error"),
    ],
)
def test_a_login_ends_in_a_state_the_operator_can_act_on(
    output: str, exit_code: int, stop_reason: str | None, state: str, reason: str | None
) -> None:
    outcome = native_connect.login_outcome(
        output,
        exit_code,
        connected=False,
        published=stop_reason == "deadline",
        stop_reason=stop_reason,
    )

    assert outcome["state"] == state
    assert outcome.get("reason") == reason
    assert outcome["error"]


def test_a_login_ends_connected_only_with_a_subscription_credential() -> None:
    assert native_connect.login_outcome(
        "Successfully logged in", 0, connected=True, published=True
    ) == {"state": "connected"}
    # Exit 0 without a well-formed login (e.g. an API key) is not connected.
    assert (
        native_connect.login_outcome("", 0, connected=False, published=True)["state"]
        == "failed"
    )


def test_a_failure_message_never_repeats_the_one_time_code() -> None:
    outcome = native_connect.login_outcome(
        CODEX_DEVICE_OUTPUT + "\nError logging in: device auth failed with status 400",
        1,
        connected=False,
        published=True,
    )

    assert "ABCD-12345" not in outcome["error"]


# --- one attempt at a time ----------------------------------------------------


def test_a_new_attempt_supersedes_the_old_one(tmp_path: Path) -> None:
    first = native_connect.begin_login("codex", tmp_path)["attempt"]
    native_connect.write_login(
        "codex",
        first,
        {"state": "waiting", "code": "OLD1-AAAAA", "expires_at": time.time() + 600},
        tmp_path,
    )
    second = native_connect.begin_login("codex", tmp_path)["attempt"]

    assert first != second
    assert not native_connect.is_current("codex", first, tmp_path)
    assert native_connect.is_current("codex", second, tmp_path)
    # The old attempt's code is gone, not shown under the new attempt.
    view = native_connect.read_login("codex", tmp_path)
    assert view["state"] == "queued"
    assert "code" not in view
    assert list(tmp_path.glob("codex.login.*.json")) == []
    # Separate containers and session copies: one cannot delete the other's.
    assert native_connect.login_label(first) != native_connect.login_label(second)


def test_a_record_from_before_attempts_is_still_read(tmp_path: Path) -> None:
    native_connect.write_state(
        "codex",
        "login",
        {"state": "waiting", "code": "C", "expires_at": time.time() + 60},
        tmp_path,
    )

    assert native_connect.read_login("codex", tmp_path)["code"] == "C"


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
    native_connect.write_login(
        "codex",
        first["attempt"],
        {"state": "waiting", "url": "u", "code": "c", "expires_at": time.time() + 600},
    )
    second = connect("codex")

    assert first["state"] == "queued"
    assert second["state"] == "waiting"  # not restarted: one code at a time
    assert sent == [("connect_native_cli", "codex", {"attempt": first["attempt"]})]
    assert read("codex")["code"] == "c"
    assert "server_time" in read("codex")


def test_a_new_code_can_be_asked_for_while_one_is_showing(api) -> None:
    app, sent = api
    connect = _endpoint(app, "/api/harness/clis/{harness}/connect", "POST")

    first = connect("codex")
    native_connect.write_login(
        "codex",
        first["attempt"],
        {"state": "waiting", "code": "c", "expires_at": time.time() + 600},
    )
    fresh = connect("codex", regenerate=True)

    assert fresh["state"] == "queued"
    assert fresh["attempt"] != first["attempt"]
    assert [kw["attempt"] for _t, _h, kw in sent] == [
        first["attempt"],
        fresh["attempt"],
    ]
    assert not native_connect.is_current("codex", first["attempt"])


def test_an_expired_code_is_regenerated_without_restarting_the_lab(api) -> None:
    app, sent = api
    connect = _endpoint(app, "/api/harness/clis/{harness}/connect", "POST")
    read = _endpoint(app, "/api/harness/clis/{harness}/connect", "GET")

    first = connect("codex")
    native_connect.write_login(
        "codex",
        first["attempt"],
        {"state": "waiting", "code": "c", "expires_at": time.time() - 1},
    )

    assert read("codex")["state"] == "expired"
    again = connect("codex")  # no regenerate flag needed: nothing is in flight
    assert again["state"] == "queued"
    assert len(sent) == 2


def test_an_unreachable_queue_leaves_no_phantom_login(api, monkeypatch) -> None:
    from fastapi import HTTPException

    from harness_lab_app import celery_app

    app, _sent = api

    def boom(*_a: Any, **_k: Any) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(celery_app, "enqueue_native_cli", boom)
    with pytest.raises(HTTPException) as exc:
        _endpoint(app, "/api/harness/clis/{harness}/connect", "POST")("codex")

    assert exc.value.status_code == 503
    assert native_connect.read_login("codex")["state"] == "failed"


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


# --- the worker task, driven through a fake CLI container ---------------------


class _FakeLogin:
    """A device login container: prints the prompt, then is silent until told.

    ``finish`` is what the CLI does when it ends on its own (approval, expiry);
    ``stop()`` is the worker cutting it short. Either ends the log stream.
    """

    def __init__(self, lines: list[str], exit_code: int = 0) -> None:
        self._queue: Queue[str | None] = Queue()
        for line in lines:
            self._queue.put(line)
        self.exit_code = exit_code
        self.exited = False
        self.stopped = False
        self.waited = False

    def finish(self, *lines: str) -> None:
        self.exited = True
        for line in lines:
            self._queue.put(line)
        self._queue.put(None)

    def iter_lines(self):
        while (line := self._queue.get()) is not None:
            yield ("info", line)

    def stop(self) -> None:
        # Like docker: stopping an exited container keeps its exit code.
        self.stopped = True
        if not self.exited:
            self.exited = True
            self.exit_code = 137
            self._queue.put(None)

    def wait(self) -> int:
        self.waited = True
        return self.exit_code


@pytest.fixture
def worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`connect_native_cli` with a fake container, a fake clock and no docker."""
    from harness_lab_app import native_auth, tasks

    monkeypatch.setattr(native_connect, "state_dir", lambda _root=None: tmp_path)
    monkeypatch.setattr(tasks, "_LOGIN_POLL_SECONDS", 0.01)
    monkeypatch.setattr(tasks, "_sweep_login_leftovers", lambda *_a, **_k: None)
    monkeypatch.setattr(native_auth, "ensure_session_dir", lambda _h: tmp_path)
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path / "runs"),
    )
    clock = {"now": 1_000_000.0, "step": 0.0}

    def _clock() -> float:
        return clock["now"]

    monkeypatch.setattr(tasks, "_wall_clock", _clock)
    # Silence costs time: each empty poll of the CLI's output advances the
    # fake clock by `step`, so a CLI that says nothing runs into the deadline.
    real_get = Queue.get

    def _get(self, block=True, timeout=None):
        try:
            return real_get(self, block, 0.01 if timeout else timeout)
        except Exception:
            clock["now"] += clock["step"]
            raise

    monkeypatch.setattr(Queue, "get", _get)
    session = {"state": "missing"}
    monkeypatch.setattr(native_auth, "session_status", lambda _h: dict(session))
    started: list[str] = []

    def run(container: _FakeLogin):
        def _start(harness: str, label: str, command: list[str]) -> _FakeLogin:
            started.append(label)
            return container

        monkeypatch.setattr(tasks, "_run_cli_container", _start)
        attempt = native_connect.begin_login("codex")["attempt"]
        return attempt, tasks.connect_native_cli("codex", attempt)

    return SimpleNamespace(
        run=run, clock=clock, session=session, started=started, tasks=tasks
    )


def test_an_approved_login_reaches_connected(worker) -> None:
    container = _FakeLogin(CODEX_DEVICE_OUTPUT.splitlines())
    container.finish("Successfully logged in")
    worker.session["state"] = "connected"

    attempt, result = worker.run(container)

    assert result["state"] == "connected"
    assert native_connect.read_login("codex")["state"] == "connected"
    assert container.waited  # the credential copy-back ran
    assert worker.started == [native_connect.login_label(attempt)]


def test_the_published_code_counts_down_from_before_the_cli_asked(
    worker, monkeypatch
) -> None:
    container = _FakeLogin(CODEX_DEVICE_OUTPUT.splitlines())
    seen: dict[str, Any] = {}
    original = native_connect.write_login

    def spy(harness: str, attempt: str, state: dict[str, Any], root=None):
        if state.get("state") == "waiting":
            seen.update(state)
            container.finish("Error logging in: device code has expired.")
        return original(harness, attempt, state, root)

    monkeypatch.setattr(native_connect, "write_login", spy)
    _attempt, result = worker.run(container)

    # Anchored on the clock read before the container started: never later
    # than the provider's own fifteen minutes.
    assert seen["expires_at"] == worker.clock["now"] + 15 * 60
    assert seen["worker_deadline"] == seen["expires_at"] + 5 * 60
    assert result["state"] == "expired"


def test_a_silent_cli_cannot_hold_the_worker_past_its_deadline(worker) -> None:
    # The CLI prints its code, then nothing. Before the fix the deadline was
    # only checked when a line arrived, so the task waited on the stream.
    container = _FakeLogin(CODEX_DEVICE_OUTPUT.splitlines())
    clock = worker.clock
    start = clock["now"]
    clock["step"] = 60.0  # a minute of silence per poll

    _attempt, result = worker.run(container)

    assert container.stopped
    assert container.waited
    assert result["state"] == "expired"
    assert clock["now"] - start <= 15 * 60 + 5 * 60 + 2 * 60
    assert native_connect.read_login("codex")["state"] == "expired"


def test_a_cli_that_never_prints_a_code_is_a_timeout(worker) -> None:
    container = _FakeLogin(["Welcome to Codex"])
    worker.clock["step"] = 30.0

    _attempt, result = worker.run(container)

    assert container.stopped
    assert result == {**result, "state": "failed", "reason": "timeout"}


def test_a_superseded_attempt_stops_its_cli_and_stays_silent(
    worker, monkeypatch
) -> None:
    container = _FakeLogin(CODEX_DEVICE_OUTPUT.splitlines())
    newer: dict[str, str] = {}
    original = native_connect.write_login

    def operator_asks_for_a_new_code(harness, attempt, state, root=None):
        record = original(harness, attempt, state, root)
        if state.get("state") == "waiting" and not newer:
            newer["attempt"] = native_connect.begin_login("codex")["attempt"]
        return record

    monkeypatch.setattr(native_connect, "write_login", operator_asks_for_a_new_code)
    _attempt, result = worker.run(container)

    assert result["state"] == "superseded"
    assert container.stopped and container.waited
    # The new attempt's record is untouched by the old worker.
    view = native_connect.read_login("codex")
    assert view["attempt"] == newer["attempt"]
    assert view["state"] == "queued"


def test_a_broken_log_stream_still_ends_the_login(worker) -> None:
    class _Broken(_FakeLogin):
        def iter_lines(self):
            yield from (("info", line) for line in CODEX_DEVICE_OUTPUT.splitlines())
            raise ConnectionError("docker log stream closed")

    container = _Broken([])
    _attempt, result = worker.run(container)

    assert result["state"] != "waiting"
    assert native_connect.read_login("codex")["state"] in {"failed", "expired"}
    assert container.waited


def test_a_docker_failure_is_reported_not_left_starting(worker, monkeypatch) -> None:
    def no_docker(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(worker.tasks, "_run_cli_container", no_docker)
    attempt = native_connect.begin_login("codex")["attempt"]
    result = worker.tasks.connect_native_cli("codex", attempt)

    assert result["state"] == "failed"
    assert "Docker daemon" in result["error"]


def test_only_a_dead_workers_leftovers_are_swept(tmp_path: Path, monkeypatch) -> None:
    # A superseded attempt that is still alive may be copying back a
    # credential approved a moment before the new code was asked for: its
    # container and session copy are its own to clean, never the sweep's.
    import os
    import sys

    from harness_lab_app import tasks

    now = 2_000_000.0
    runs = tmp_path / "runs"
    for name, age in (
        ("connect-codex-login-dead", 3_600),
        ("connect-codex-login-live", 30),
        ("connect-codex-login-new", 3_600),
    ):
        (runs / name).mkdir(parents=True)
        os.utime(runs / name, (now - age, now - age))

    class _Container:
        def __init__(self, name: str, created: str) -> None:
            self.name = name
            self.attrs = {"Created": created}
            self.stopped = False

        def stop(self, timeout: int) -> None:
            self.stopped = True

        def remove(self, force: bool) -> None:
            pass

    import datetime as dt

    def iso(age: float) -> str:
        moment = dt.datetime.fromtimestamp(now - age, tz=dt.UTC)
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"

    dead = _Container("harness-run-connect-codex-login-dead", iso(3_600))
    live = _Container("harness-run-connect-codex-login-live", iso(30))
    containers = SimpleNamespace(list=lambda **_k: [dead, live])
    monkeypatch.setitem(
        sys.modules,
        "docker",
        SimpleNamespace(from_env=lambda: SimpleNamespace(containers=containers)),
    )
    monkeypatch.setattr(tasks, "_wall_clock", lambda: now)
    monkeypatch.setattr(
        tasks, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs)
    )

    tasks._sweep_login_leftovers(
        "codex", keep="connect-codex-login-new", older_than=25 * 60
    )

    assert dead.stopped and not live.stopped
    assert sorted(p.name for p in runs.iterdir()) == [
        "connect-codex-login-live",
        "connect-codex-login-new",
    ]
