"""Connect and test a native agent CLI from the UI (System → Agent CLIs).

The lab's subscription harnesses log in with the CLI's own flow, never with a
token the lab handles (ADR-0094). Until now that meant a terminal
(`make auth-codex`). This module drives the same official flow from the web
UI:

* **connect** — the worker starts the CLI's device login in a runner container
  whose only mount is a private copy of the provider's session directory. The
  CLI prints a URL and a one-time code; the UI shows them; the operator approves
  in their own browser; the CLI writes its credential; the worker moves it into
  the store. The lab never sees a password, and never sees the token's value —
  it moves a file the CLI wrote.
* **test** — the worker runs one minimal headless prompt with exactly the
  credential and environment a run gets, and reports the model that answered.
  "There is a credential file" is not "the provider serves this account": the
  Gemini CLI kept a valid login for months after Google stopped serving
  personal plans through it.

Both are long-lived and need the docker socket, which only the worker holds
(ADR-0002), and Celery here has no result backend. Progress is therefore a
small JSON file per harness under ``data/app/native-connect/``, written by
the worker (and by the API when it queues a task) and read by the API. It
never holds a credential.

Every login is one **attempt**, with its own id, container and private
session copy. Only the attempt the record names may write it: asking for a new
code supersedes the running attempt, which then stops its container and stays
silent, so two attempts never share a session directory or overwrite each
other's state.

The code's lifetime is the provider's (fifteen minutes, printed by the CLI)
and the lab never extends it. The worker gets a separate, longer budget to
finish the token exchange and clean up. A record still saying ``waiting`` once
the code's lifetime has passed is shown as ``expired`` whatever became of the
worker (:func:`login_view`), so an expired code is never presented as valid.

Pure helpers live here; the Celery tasks that call them live in ``tasks.py``.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


#: Harnesses that can be connected from the UI, and how.
CONNECTABLE: dict[str, dict[str, Any]] = {
    "codex": {
        # Official device-code flow (codex-cli 0.171.0): prints a URL and a
        # code that expires in 15 minutes, then exits 0 once approved.
        "login": ["codex", "login", "--device-auth"],
        # The provider's lifetime for the code. The countdown the UI shows.
        "code_lifetime_seconds": 15 * 60,
        # The worker's margin past it, for an approval that lands in the last
        # seconds (token exchange, credential copy) and for cleanup. Never
        # shown to the operator as time left on the code.
        "grace_seconds": 5 * 60,
        # How long the CLI may take to print the URL and code at all.
        "code_wait_seconds": 2 * 60,
    },
}

#: A login the API queued but no worker started within this many seconds is
#: reported as a timeout: the interactive worker is down or busy.
START_TIMEOUT_SECONDS = 120

#: After the code expires, how long the UI keeps asking whether an approval
#: from its last seconds went through (the token exchange takes seconds; the
#: CLI's own clock starts a moment after the lab's anchor).
SETTLE_SECONDS = 60

#: Why a login did not connect. ``expired`` is its own state; the rest are
#: ``failed`` with one of these reasons.
FAILURE_REASONS = ("rejected", "timeout", "connection", "error")

#: Harnesses whose provider no longer serves personal plans through the CLI.
#: Kept in the catalog for organizations with an enterprise license.
UNAVAILABLE: dict[str, str] = {
    "gemini": (
        "Google stopped serving Gemini CLI for Google AI Pro/Ultra and free "
        "accounts on 2026-06-18 (replaced by Antigravity CLI). It still works "
        "with a Code Assist Standard/Enterprise license or a paid API key."
    ),
}

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_URL = re.compile(r"https://\S+")
# A device code: two to three groups of letters/digits joined by dashes.
_CODE = re.compile(r"\b[A-Z0-9]{4,5}(?:-[A-Z0-9]{4,5}){1,2}\b")

#: The one prompt a connection test sends. Tiny on purpose: it spends one
#: request of the plan's quota and nothing else.
TEST_PROMPT = "Reply with exactly the word: ok"


def state_dir(root: Path | None = None) -> Path:
    """Where progress records live: ``data/app/native-connect/`` by default.

    Not in the session store: the API mounts that read-only, and these records
    hold no credential, only a state, a public verification URL and a
    one-time code. ``data/app/`` is shared read-write by the API and the
    workers and is git-ignored.
    """
    if root is not None:
        return root
    from harness_lab_app.config import get_settings

    return get_settings().repo_root / "data" / "app" / "native-connect"


def state_path(harness: str, kind: str, root: Path | None = None) -> Path:
    """``<state_dir>/<harness>.<kind>.json`` (kind: ``login`` | ``test``)."""
    return state_dir(root) / f"{harness}.{kind}.json"


def write_state(
    harness: str, kind: str, state: dict[str, Any], root: Path | None = None
) -> dict[str, Any]:
    """Persist a progress record atomically (the API may read it mid-write)."""
    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    record = {**state, "harness": harness, "kind": kind, "updated_at": time.time()}
    path = state_path(harness, kind, root)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(path)
    return record


def read_state(harness: str, kind: str, root: Path | None = None) -> dict[str, Any]:
    """The last progress record, or ``{"state": "idle"}``. Never raises."""
    try:
        data = json.loads(state_path(harness, kind, root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"harness": harness, "kind": kind, "state": "idle"}
    return data if isinstance(data, dict) else {"state": "idle"}


def _attempt_kind(attempt: str) -> str:
    return f"login.{attempt}"


def begin_login(harness: str, root: Path | None = None) -> dict[str, Any]:
    """Start a new attempt: point the login record at it, drop older attempts.

    Only the API (and a disconnect) writes ``<harness>.login.json``; a worker
    writes its own attempt's record (:func:`write_login`). So a worker that is
    being superseded can never overwrite the pointer to the attempt replacing
    it — file locks would not help: they exclude nothing on the Docker Desktop
    bind both containers share.
    """
    for old in state_dir(root).glob(f"{harness}.login.*.json"):
        old.unlink(missing_ok=True)
    return write_state(
        harness, "login", {"state": "queued", "attempt": new_attempt()}, root
    )


def write_login(
    harness: str, attempt: str, state: dict[str, Any], root: Path | None = None
) -> dict[str, Any]:
    """The worker's write: to its own attempt's record, never the pointer."""
    return write_state(
        harness, _attempt_kind(attempt), {**state, "attempt": attempt}, root
    )


def read_login(
    harness: str, root: Path | None = None, now: float | None = None
) -> dict[str, Any]:
    """The current login as the UI should see it (:func:`login_view`).

    Follows the pointer to the attempt it names; until that attempt's worker
    writes anything, the pointer itself (``queued``) is the answer. A record
    from before attempts existed has no pointer and is read as it is.
    """
    record = read_state(harness, "login", root)
    attempt = str(record.get("attempt") or "")
    if attempt:
        own = read_state(harness, _attempt_kind(attempt), root)
        if own.get("state") != "idle":  # idle: the worker has not written yet
            record = own
    return login_view({**record, "kind": "login"}, now)


def new_attempt() -> str:
    """A fresh login attempt id: short, unique, safe in a container name."""
    return secrets.token_hex(4)


def login_label(attempt: str) -> str:
    """The executor label of one attempt: its container and session copy.

    Unique per attempt so a superseded attempt's cleanup can never delete the
    session copy the next attempt's CLI is writing its credential into.
    """
    return f"login-{attempt}" if attempt else "login"


def is_current(harness: str, attempt: str, root: Path | None = None) -> bool:
    """True while ``attempt`` is the login the record names.

    A newer attempt (the operator asked for a new code) supersedes it: the
    older one must stop and must not write the record again.
    """
    return str(read_state(harness, "login", root).get("attempt") or "") == attempt


def login_view(record: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """What the API shows for a login record, judged on the server's clock.

    * ``waiting`` past ``expires_at`` is ``expired``, and its code is dropped:
      a record the worker never closed (it died, the stream broke) must not
      keep presenting a dead code. ``settling`` stays true while the worker
      may still report an approval that landed in the last seconds.
    * ``queued``/``starting`` that no worker moved on is ``failed/timeout``.

    ``server_time`` lets the browser correct its own clock: the countdown is
    ``expires_at - server_time`` at the moment the response arrived.
    """
    now = time.time() if now is None else now
    view = dict(record)
    state = view.get("state")
    if state == "waiting" and float(view.get("expires_at") or 0) <= now:
        expired_at = float(view.get("expires_at") or 0)
        view.pop("code", None)
        view.update(
            state="expired",
            settling=now
            < min(expired_at + SETTLE_SECONDS, float(view.get("worker_deadline") or 0)),
            error="The code's lifetime ran out before it was approved.",
        )
    elif state in {"queued", "starting"} and now >= float(
        view.get("worker_deadline")
        or float(view.get("updated_at") or 0) + START_TIMEOUT_SECONDS
    ):
        view.update(
            state="failed",
            reason="timeout",
            error=(
                "No worker picked up the login: is the interactive worker "
                "running? (Health)"
                if state == "queued"
                else "The login stopped before the CLI printed a code."
            ),
        )
    view["server_time"] = now
    return view


def login_in_flight(state: dict[str, Any], now: float | None = None) -> bool:
    """True while a login is queued, starting, or showing an unexpired code.

    Judged on :func:`login_view`: a record the worker never closed (it died
    mid-login) stops counting once its code expired, so a new login may start.
    """
    return login_view(state, now)["state"] in {"queued", "starting", "waiting"}


# What the codex CLI (0.171.0) prints when a device login ends without a
# credential, read from its binary: the provider's verdict, in its own words.
_EXPIRED = re.compile(r"(?i)device code has expired|device auth timed out")
_REJECTED = re.compile(
    r"(?i)access_denied|device auth failed with status|device code exchange failed"
    r"|device code login is not enabled|device code request failed with status 4\d\d"
)
_CONNECTION = re.compile(
    r"(?i)error sending request|dns error|failed to lookup address"
    r"|connection (?:refused|reset|closed)|network is unreachable|tls handshake"
    r"|request failed with status 5\d\d"
)


def login_outcome(
    output: str,
    exit_code: int,
    *,
    connected: bool,
    published: bool,
    stop_reason: str | None = None,
) -> dict[str, Any]:
    """The final record of a device login: connected, expired, or failed/<reason>.

    ``stop_reason`` is the worker's, when it stopped the CLI itself:
    ``no_code`` (nothing printed within ``code_wait_seconds``) or ``deadline``
    (the code's lifetime and the worker's margin both ran out). The provider's
    own line is kept as ``error`` with the one-time code masked.
    """
    if exit_code == 0 and connected:
        return {"state": "connected"}
    lines = [line.strip() for line in _ANSI.sub("", output).splitlines()]

    def said(pattern: re.Pattern[str]) -> str:
        line = next((line for line in reversed(lines) if pattern.search(line)), "")
        return _CODE.sub("••••", line)[:400]

    if words := said(_EXPIRED):
        return {"state": "expired", "error": words}
    if words := said(_REJECTED):
        return {"state": "failed", "reason": "rejected", "error": words}
    if words := said(_CONNECTION):
        return {"state": "failed", "reason": "connection", "error": words}
    if stop_reason == "deadline" and published:
        return {
            "state": "expired",
            "error": "The code's lifetime ran out before it was approved.",
        }
    if stop_reason in {"deadline", "no_code"}:
        return {
            "state": "failed",
            "reason": "timeout",
            "error": (
                "The CLI printed no code in time; check the runner's network "
                "to auth.openai.com."
                if not published
                else "The login did not finish within the lab's margin."
            ),
        }
    if exit_code == 0:
        return {
            "state": "failed",
            "reason": "error",
            "error": "The CLI exited without writing a subscription login.",
        }
    tail = _CODE.sub("••••", "\n".join(line for line in lines if line)[-400:])
    return {
        "state": "failed",
        "reason": "error",
        "error": f"login exited {exit_code}: {tail}",
    }


def parse_device_prompt(text: str) -> dict[str, str] | None:
    """The verification URL and one-time code from a CLI's login output.

    ``None`` until both have been printed. Colour codes are stripped first: the
    codex CLI paints the URL and the code in blue.
    """
    clean = _ANSI.sub("", text)
    url = next(
        (
            m.group(0).rstrip(".,)")
            for m in _URL.finditer(clean)
            if "device" in m.group(0)
        ),
        None,
    ) or next((m.group(0).rstrip(".,)") for m in _URL.finditer(clean)), None)
    code = _CODE.search(clean)
    if not url or not code:
        return None
    return {"url": url, "code": code.group(0)}


_PRINTED_LIFETIME = re.compile(r"(?i)expires in (\d{1,3}) minutes?")


def printed_lifetime(text: str) -> int | None:
    """The code's lifetime in seconds as the CLI printed it, if it did.

    The provider owns the lifetime; the configured one is only the fallback
    for a CLI that stops printing it.
    """
    match = _PRINTED_LIFETIME.search(_ANSI.sub("", text))
    return int(match.group(1)) * 60 if match else None


def clear_login(harness: str, root: Path | None = None) -> dict[str, Any]:
    """Back to ``idle``: drop every attempt, so a running one stops itself."""
    for old in state_dir(root).glob(f"{harness}.login.*.json"):
        old.unlink(missing_ok=True)
    return write_state(harness, "login", {"state": "idle"}, root)


def test_command(harness: str, model: str = "") -> list[str]:
    """One minimal headless request, with the flags a run uses in the runner."""
    if harness == "codex":
        cmd = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if model:
            cmd += ["-m", model]
        return [*cmd, TEST_PROMPT]
    if harness == "gemini":
        cmd = ["gemini", "--output-format", "stream-json", "--approval-mode", "plan"]
        if model:
            cmd += ["-m", model]
        return [*cmd, "-p", TEST_PROMPT]
    raise ValueError(f"no connection test for {harness!r}")


def summarize_test(harness: str, output: str, exit_code: int) -> dict[str, Any]:
    """Turn a test's raw output into ``ok``/``failed`` with the provider's words.

    Reads the CLI's own JSON events (codex ``turn.completed`` / ``turn.failed``,
    gemini ``result``); anything else is reported verbatim, trimmed, so a
    provider message the lab has never seen still reaches the operator.
    """
    clean = _ANSI.sub("", output)
    answer = ""
    model = ""
    error = ""
    for line in clean.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            if not error and re.search(
                r"(?i)error|unauthori[sz]ed|not supported|exhausted", line
            ):
                error = line
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if harness == "codex":
            if kind == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    answer = str(item.get("text") or "")
            elif kind == "turn.failed":
                err = event.get("error") or {}
                error = str(err.get("message") if isinstance(err, dict) else err)
        elif harness == "gemini":
            if kind == "init":
                model = str(event.get("model") or "")
            elif kind == "message" and event.get("role") == "assistant":
                answer += str(event.get("content") or "")
            elif kind == "result":
                if event.get("status") == "error":
                    err = event.get("error") or {}
                    error = str(err.get("message") if isinstance(err, dict) else err)
                models = (event.get("stats") or {}).get("models") or {}
                if models:
                    model = max(
                        models, key=lambda n: (models[n] or {}).get("output_tokens", 0)
                    )
    ok = exit_code == 0 and not error and bool(answer.strip())
    return {
        "state": "ok" if ok else "failed",
        "answer": answer.strip()[:200],
        "model": model,
        "error": "" if ok else (error or f"exit code {exit_code}")[:500],
    }
