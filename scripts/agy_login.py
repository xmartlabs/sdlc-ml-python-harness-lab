#!/usr/bin/env python3
"""Drive the Antigravity CLI's own Google sign-in inside a runner container.

ADR-0097. Run by the worker (``connect_native_cli``) as the command of a lab
container whose ``HOME`` is a fresh directory the worker owns. It does what an
operator would do at a terminal, and nothing more:

1. starts ``agy`` with no arguments on a pseudo-terminal (agy 1.5.14 shows its
   sign-in menu only on a TTY) and picks "1. Google OAuth";
2. prints the ``accounts.google.com`` URL agy shows, as one helper record;
3. waits for the authorization code the operator pasted in the UI, which the
   worker drops into ``$AGY_LOGIN_EXCHANGE/code`` (deleted once read), and
   types it into agy's prompt;
4. reports success once agy has written its token file, or agy's own error.

The helper never reads, prints or transmits the token: the worker copies the
files agy wrote (``agy_account.save_account``). Every record it prints is one
line ``@@agy-login {json}``; agy's screen is not forwarded, so nothing it
renders reaches the worker log. Standard library only: it runs on the image's
Python before anything else is imported.

Exit codes: 0 signed in, 2 agy refused the code, 3 timed out, 4 agy exited or
failed otherwise.
"""

from __future__ import annotations

import contextlib
import json
import os
import pty
import re
import select
import signal
import sys
import time
from pathlib import Path

PREFIX = "@@agy-login "
TOKEN_FILE = ".gemini/antigravity-cli/antigravity-oauth-token"
URL_RE = re.compile(r"https://accounts\.google\.com/o/oauth2/[^\s\"'<>\x07\x1b]+")
# agy prints the sign-in URL as a terminal hyperlink (OSC 8) whose *target* is
# the whole URL, while the visible text is wrapped to the screen width. The
# target is read from the raw bytes: ``ESC ] 8 ; params ; URL BEL``.
OSC8_RE = re.compile(
    rb"\x1b\]8;[^;\x07\x1b]*;(https://accounts\.google\.com/o/oauth2/[^\x07\x1b]+)(?:\x07|\x1b\\)"
)
STATE_RE = re.compile(r"[?&]state=[A-Za-z0-9_\-]+")
ANSI_RE = re.compile(
    r"\x1b\[[0-9;?<>=]*[A-Za-z~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>]"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# agy 1.5.14's own words, read from its binary and from a refused code.
MENU_RE = re.compile(r"Select login method")
ERROR_RE = re.compile(
    r"(Got an error:[^\n]*|token exchange failed[^\n]*|authentication failed[^\n]*"
    r"|cancelled: no authorization code was entered)"
)


def emit(**record: object) -> None:
    sys.stdout.write(PREFIX + json.dumps(record) + "\n")
    sys.stdout.flush()


def clean(raw: bytes) -> str:
    return ANSI_RE.sub("", raw.decode("utf-8", "replace")).replace("\r", "\n")


def sign_in_url(raw: bytes) -> str:
    """The complete sign-in URL: the hyperlink target, else the visible text.

    Only a URL that carries agy's ``state`` parameter is returned: a target
    or a line cut short by a read boundary is not a URL to show yet.
    """
    for target in OSC8_RE.finditer(raw):
        linked = target.group(1).decode("utf-8", "replace")
        if STATE_RE.search(linked):
            return linked
    joined = re.sub(r"[ \t]*\n[ \t]*", "", clean(raw))
    visible = URL_RE.search(joined)
    if not visible:
        return ""
    url = visible.group(0)
    state = STATE_RE.search(url)
    return url[: state.end()] if state else ""


def main() -> int:
    home = Path(os.environ.get("HOME", "/tmp/agy-home"))  # nosec B108
    exchange = Path(os.environ.get("AGY_LOGIN_EXCHANGE", str(home / ".exchange")))
    exchange.mkdir(parents=True, exist_ok=True)
    url_wait = float(os.environ.get("AGY_LOGIN_URL_WAIT", "90"))
    code_wait = float(os.environ.get("AGY_LOGIN_CODE_WAIT", "900"))
    token = home / TOKEN_FILE
    workdir = Path(os.environ.get("AGY_LOGIN_WORKDIR", str(home / "work")))
    workdir.mkdir(parents=True, exist_ok=True)

    pid, fd = pty.fork()
    if pid == 0:  # the child: agy on the terminal, in an empty directory
        os.chdir(workdir)
        os.environ.setdefault("TERM", "xterm-256color")
        os.environ["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
        os.execvp("agy", ["agy"])

    # `raw` is agy's terminal output (a bounded tail), `screen` the same with
    # control sequences stripped. Both restart when the code is typed, so the
    # outcome is judged on what agy printed *after* it, never on the menu.
    screen = ""
    raw = b""
    started = time.time()
    phase = "menu"  # menu -> url -> code -> code_sent
    menu_answered_at = 0.0
    code_sent_at = 0.0
    deadline = started + url_wait

    def finish(code: int, **record: object) -> int:
        emit(**record)
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            try:
                if os.waitpid(pid, os.WNOHANG) != (0, 0):
                    break
            except ChildProcessError:
                break
            time.sleep(0.1)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        return code

    emit(state="starting")
    while True:
        now = time.time()
        if now >= deadline:
            if phase in ("menu", "url"):
                return finish(
                    3,
                    state="failed",
                    reason="timeout",
                    error="agy printed no sign-in URL in time.",
                )
            return finish(
                3, state="expired", error="The sign-in was not completed in time."
            )
        ready, _, _ = select.select([fd], [], [], 0.5)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                if has_token(token):
                    return finish(0, state="connected", email=found_email(screen))
                error = last_error(screen)
                return finish(
                    4,
                    state="failed",
                    reason="rejected" if error else "error",
                    error=error or "agy exited before the sign-in finished.",
                )
            raw = (raw + chunk)[-65536:]
            screen = clean(raw)

        if phase == "menu":
            if MENU_RE.search(screen) and not menu_answered_at:
                time.sleep(0.5)
                os.write(fd, b"\r")  # "1. Google OAuth" is the highlighted option
                menu_answered_at = time.time()
            if menu_answered_at or "oauth2/auth" in screen:
                phase = "url"
        if phase == "url":
            url = sign_in_url(raw)
            if url:
                phase = "code"
                deadline = time.time() + code_wait
                emit(state="waiting", url=url, expires_in=int(code_wait))
        if phase == "code":
            code_file = exchange / "code"
            if code_file.is_file():
                try:
                    code = code_file.read_text(encoding="utf-8").strip()
                finally:
                    code_file.unlink(missing_ok=True)
                if code:
                    raw, screen = b"", ""
                    os.write(fd, code.encode("utf-8"))
                    time.sleep(0.3)
                    os.write(fd, b"\r")
                    code_sent_at = time.time()
                    phase = "code_sent"
                    emit(state="exchanging")
        if phase == "code_sent":
            if has_token(token):
                # agy writes the token, then finishes its own start-up; give it
                # a moment so every file of the sign-in is on disk.
                time.sleep(3)
                return finish(0, state="connected", email=found_email(screen))
            error = last_error(screen)
            if error:
                return finish(2, state="failed", reason="rejected", error=error)
            if time.time() - code_sent_at > 60:
                return finish(
                    3,
                    state="failed",
                    reason="timeout",
                    error="agy did not confirm the sign-in after the code.",
                )


def has_token(token: Path) -> bool:
    try:
        return token.is_file() and token.stat().st_size > 0
    except OSError:
        return False


def last_error(text: str) -> str:
    matches = ERROR_RE.findall(text)
    return matches[-1].strip()[:300] if matches else ""


def found_email(text: str) -> str:
    matches = EMAIL_RE.findall(text)
    return matches[-1] if matches else ""


if __name__ == "__main__":
    sys.exit(main())
