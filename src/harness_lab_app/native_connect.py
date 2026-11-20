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

Pure helpers live here; the Celery tasks that call them live in ``tasks.py``.
"""

from __future__ import annotations

import json
import re
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
        "timeout_seconds": 15 * 60,
    },
}

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


def login_in_flight(state: dict[str, Any], now: float | None = None) -> bool:
    """True while a login is queued, starting, or showing an unexpired code.

    A record older than the code's lifetime plus a margin is stale (a worker
    that died mid-login), so a new login may start.
    """
    now = time.time() if now is None else now
    kind = state.get("state")
    updated = float(state.get("updated_at") or 0)
    if kind in {"queued", "starting"}:
        return now - updated < 120
    if kind == "waiting":
        return float(state.get("expires_at") or 0) > now
    return False


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
