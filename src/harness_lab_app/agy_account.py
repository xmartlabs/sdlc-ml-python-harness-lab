"""The Antigravity CLI on the operator's own Google sign-in (Google AI Pro/Ultra).

ADR-0097. The second way ``antigravity_cli`` runs, beside the Gemini API key of
ADR-0096: the CLI's **own** "Google OAuth" sign-in, done once from the UI the
way codex's device login is (ADR-0094), then reused by every run on the plan's
quota. The lab never copies a token from the operator's machine, never touches
the macOS keychain, never rotates accounts: the credential is the one agy
itself writes in a lab container after the operator signs in.

What agy 1.5.14 does, verified on the binary in the runner image:

* ``agy`` with no arguments on a TTY and no saved sign-in shows "Select login
  method: 1. Google OAuth, 2. Use a Google Cloud project", then prints a
  ``https://accounts.google.com/o/oauth2/auth?...`` URL (PKCE, redirect to
  ``https://antigravity.google/oauth-callback``) and waits for the
  authorization code to be pasted. No 60-second limit applies there (that is
  the ``-p`` print-mode prompt); a wrong code prints ``token exchange failed:
  oauth2: "invalid_grant"``.
* With no D-Bus session (a container), the token goes to a **file**: the log
  says "Using file-based token storage because no D-Bus session bus
  detected". At start-up agy looks for ``$HOME/.gemini/antigravity-cli/
  antigravity-oauth-token`` and ``$HOME/.config/antigravity/
  credential-user-scope`` (strace of a fresh start).

The session is kept as an allow-list of files under the CLI's home, exactly
like codex/gemini (:mod:`harness_lab_app.native_auth`): what the login wrote
**outside** the CLI's conversation, log, cache and builtin directories is
recorded in a manifest and is all a run's private home receives. Nothing else
of a run's home (conversations, memories, plugins, skills it installed) ever
reaches another run.

Layout (``<HARNESS_LAB_AUTH_DIR>/antigravity_cli/account/``, 0700)::

    home/.gemini/antigravity-cli/antigravity-oauth-token   ← agy's own token
    home/.config/antigravity/credential-user-scope
    home/...                                                ← anything else the
                                                              login wrote that
                                                              the manifest lists
    manifest.json   {"files": [...], "email": ..., "saved_at": ...}
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Any

from harness_lab_app.native_auth import auth_root

HARNESS = "antigravity_cli"

#: The two ways an antigravity_cli run authenticates.
AUTH_ACCOUNT = "account"  # the operator's Google sign-in: the plan's quota
AUTH_API_KEY = "api_key"  # a Gemini API key: per-token billing (ADR-0096)

#: Where a run's private copy of the account home is mounted in the runner.
CONTAINER_ACCOUNT_HOME = "/run/harness-auth/antigravity_cli/home"
#: Where the login container's helper publishes the URL / reads the code.
CONTAINER_EXCHANGE_DIR = "/run/harness-auth/antigravity_cli/exchange"

#: The files agy 1.5.14 looks for at start-up (strace, no D-Bus). The token is
#: required for "connected"; the scope file is kept when written.
TOKEN_FILE = ".gemini/antigravity-cli/antigravity-oauth-token"
SCOPE_FILE = ".config/antigravity/credential-user-scope"

#: Directories of the CLI's home that hold a session's *work*, never its
#: credential: conversations, logs, crash dumps, caches, builtin skills and
#: plugins, per-project state. A login writes some of them too; none of it is
#: kept, so nothing one run (or the login) did reaches the next run.
_WORK_PREFIXES: tuple[str, ...] = (
    ".gemini/antigravity-cli/conversations/",
    ".gemini/antigravity-cli/implicit/",
    ".gemini/antigravity-cli/builtin/",
    ".gemini/antigravity-cli/log/",
    ".gemini/antigravity-cli/crashes/",
    ".gemini/antigravity-cli/cache/",
    ".gemini/antigravity-cli/plugins/",
    ".gemini/antigravity-cli/plugin_data/",
    ".gemini/antigravity-cli/brain/",
    ".gemini/antigravity/",
    ".gemini/config/projects/",
    ".gemini/skills/",
    ".gemini/plugins/",
    ".agents/",
    ".cache/",
)
_WORK_FILES: frozenset[str] = frozenset(
    {
        ".gemini/antigravity-cli/cli.log",
        ".gemini/antigravity-cli/conversation_summaries.db",
        ".gemini/antigravity-cli/conversation_summaries.db-wal",
        ".gemini/antigravity-cli/conversation_summaries.db-shm",
        ".gemini/antigravity-cli/settings.json",
        ".gemini/antigravity-cli/hooks.json",
        ".gemini/config/mcp_config.json",
        ".gemini/config/.migrated",
        ".gemini/GEMINI.md",
        ".gemini/AGENTS.md",
    }
)
#: A manifest never lists more than this many files, nor any larger than this:
#: a sign-in writes a handful of small files.
_MAX_FILES = 32
_MAX_FILE_BYTES = 256 * 1024

_MANIFEST = "manifest.json"
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def account_dir(root: Path | None = None) -> Path:
    """``<HARNESS_LAB_AUTH_DIR>/antigravity_cli/account``."""
    return (root or auth_root()) / HARNESS / "account"


def account_home(root: Path | None = None) -> Path:
    """The stored copy of agy's home after a sign-in (credential files only)."""
    return account_dir(root) / "home"


def manifest_path(root: Path | None = None) -> Path:
    return account_dir(root) / _MANIFEST


def is_session_file(relative: str) -> bool:
    """True for a file of agy's home that may belong to a sign-in.

    Everything a session *works* with is excluded (:data:`_WORK_PREFIXES`,
    :data:`_WORK_FILES`); so is anything that is not a plain relative path.
    """
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    text = str(path)
    if text in _WORK_FILES:
        return False
    return not any(text.startswith(prefix) for prefix in _WORK_PREFIXES)


def session_files(home: Path) -> list[str]:
    """The relative paths under ``home`` that :func:`is_session_file` keeps.

    Symlinks, oversized files and anything past :data:`_MAX_FILES` are left
    out: a sign-in writes a few small regular files.
    """
    found: list[str] = []
    if not home.is_dir():
        return found
    for path in sorted(home.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(home).as_posix()
        if not is_session_file(relative):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        found.append(relative)
        if len(found) >= _MAX_FILES:
            break
    return found


def _read_manifest(root: Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(manifest_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def manifest_files(root: Path | None = None) -> list[str]:
    """The session files the stored sign-in consists of (validated)."""
    files = _read_manifest(root).get("files")
    if not isinstance(files, list):
        return []
    return [str(f) for f in files if isinstance(f, str) and is_session_file(f)][
        :_MAX_FILES
    ]


def has_token(home: Path) -> bool:
    """True when agy's token file exists and is not empty."""
    token = home / TOKEN_FILE
    try:
        return token.is_file() and token.stat().st_size > 0
    except OSError:
        return False


def save_account(
    login_home: Path, *, email: str = "", root: Path | None = None
) -> dict[str, Any]:
    """Keep what a successful sign-in wrote; drop every other file.

    ``login_home`` is the login container's home (a run directory the worker
    owns). Only :func:`session_files` cross into the store, through a fresh
    directory swapped in at the end, so a half-copied sign-in is never read.
    Raises ``ValueError`` when the sign-in left no token.
    """
    if not has_token(login_home):
        raise ValueError("The sign-in did not leave a token for the Antigravity CLI.")
    files = session_files(login_home)
    base = account_dir(root)
    base.parent.mkdir(parents=True, exist_ok=True)
    base.parent.chmod(0o700)
    fresh = base.with_name(f".account-{os.getpid()}-{int(time.time())}")
    shutil.rmtree(fresh, ignore_errors=True)
    (fresh / "home").mkdir(parents=True)
    fresh.chmod(0o700)
    for relative in files:
        target = fresh / "home" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        for parent in target.relative_to(fresh).parents:
            if str(parent) != ".":
                (fresh / parent).chmod(0o700)
        target.write_bytes((login_home / relative).read_bytes())
        target.chmod(0o600)
    record = {
        "files": files,
        "email": email if _EMAIL.fullmatch(email or "") else "",
        "saved_at": time.time(),
        # Which sign-in this is: a run hands a refreshed token back only to the
        # sign-in it was staged from (:func:`return_run_home`).
        "generation": secrets.token_hex(8),
    }
    (fresh / _MANIFEST).write_text(json.dumps(record), encoding="utf-8")
    (fresh / _MANIFEST).chmod(0o600)
    old = base.with_name(f".account-old-{os.getpid()}-{int(time.time())}")
    if base.exists():
        base.replace(old)
    fresh.replace(base)
    shutil.rmtree(old, ignore_errors=True)
    return account_status(root)


def remove_account(root: Path | None = None) -> dict[str, Any]:
    """Forget the stored sign-in (the lab's copy only; nothing on Google's side)."""
    shutil.rmtree(account_dir(root), ignore_errors=True)
    return account_status(root)


def account_status(root: Path | None = None) -> dict[str, Any]:
    """Whether a sign-in is stored, read from its manifest and token's presence."""
    manifest = _read_manifest(root)
    connected = bool(manifest) and has_token(account_home(root))
    return {
        "connected": connected,
        "email": str(manifest.get("email") or "") if connected else "",
        "saved_at": manifest.get("saved_at") if connected else None,
        "files": len(manifest_files(root)) if connected else 0,
    }


def generation(root: Path | None = None) -> str:
    """The stored sign-in's identity, new on every :func:`save_account`."""
    return str(_read_manifest(root).get("generation") or "")


def _generation_marker(home: Path) -> Path:
    """Beside the run's home, never inside it: the agent cannot rewrite it."""
    return home.with_name(home.name + ".generation")


def stage_run_home(destination: Path, root: Path | None = None) -> Path:
    """A run's private agy home: the manifest's files, nothing else.

    The run writes its conversations, logs and databases there; all of it is
    discarded with the run (:func:`return_run_home`). Only a refreshed token
    goes back to the store, and only to the sign-in it was copied from: the
    generation is recorded beside the home (outside the mounted directory).
    """
    source = account_home(root)
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    destination.chmod(0o700)
    marker = _generation_marker(destination)
    marker.write_text(generation(root), encoding="utf-8")
    marker.chmod(0o600)
    for relative in manifest_files(root):
        origin = source / relative
        if not origin.is_file() or origin.is_symlink():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(origin.read_bytes())
        target.chmod(0o600)
    return destination


def return_run_home(staged: Path, root: Path | None = None) -> bool:
    """Write refreshed session files back to the store, then drop the run's home.

    Only files the manifest already lists return (a run cannot add a file to
    the sign-in), only regular non-empty files under the size cap, and only
    to the sign-in the run was staged from: a Sign out, or a new sign-in
    (another account, or a fix for a broken token) made during the run, wins.
    A path whose parents leave the run's home (a symlinked directory) is
    skipped. Returns True when the store changed. Never raises.
    """
    updated = False
    marker = _generation_marker(staged)
    try:
        if not account_status(root)["connected"]:
            return False
        try:
            staged_from = marker.read_text(encoding="utf-8").strip()
        except OSError:
            staged_from = ""
        if not staged_from or staged_from != generation(root):
            return False
        store = account_home(root)
        base = staged.resolve()
        for relative in manifest_files(root):
            fresh = staged / relative
            stored = store / relative
            if fresh.is_symlink() or not fresh.is_file():
                continue
            if not fresh.resolve().is_relative_to(base):
                continue
            size = fresh.stat().st_size
            if size == 0 or size > _MAX_FILE_BYTES:
                continue
            data = fresh.read_bytes()
            if stored.is_file() and stored.read_bytes() == data:
                continue
            temporary = stored.with_name(stored.name + ".tmp")
            temporary.write_bytes(data)
            temporary.chmod(0o600)
            temporary.replace(stored)
            updated = True
    except OSError:
        updated = False
    finally:
        shutil.rmtree(staged, ignore_errors=True)
        marker.unlink(missing_ok=True)
    return updated


def auth_mode(root: Path | None = None, *, key_configured: bool | None = None) -> str:
    """How the next antigravity_cli run authenticates: account, api_key or "".

    The Google sign-in wins when both exist: it is what the operator connected
    to run on their plan; the API key stays as the fallback for organizations
    and for keys with paid limits.
    """
    if account_status(root)["connected"]:
        return AUTH_ACCOUNT
    if key_configured is None:
        from harness_lab_app.api_key_auth import key_status

        key_configured = bool(key_status(root)["configured"])
    return AUTH_API_KEY if key_configured else ""


# ---------------------------------------------------------------------------
# The login's progress, as the helper in the container reports it
# ---------------------------------------------------------------------------

#: What the in-container helper prints, one JSON object per line, prefixed so
#: the worker can tell its records apart from agy's own screen output.
HELPER_PREFIX = "@@agy-login "

#: How long the operator has to sign in and paste the code. agy itself sets no
#: limit in its interactive login; this is the lab's, so a forgotten login does
#: not hold a container forever.
CODE_LIFETIME_SECONDS = 15 * 60
#: The worker's margin past it: the code exchange and the copy back.
GRACE_SECONDS = 2 * 60
#: How long agy may take to print the sign-in URL at all.
URL_WAIT_SECONDS = 90

_CODE_CHARS = re.compile(r"^[A-Za-z0-9/_\-.~]{10,512}$")


def validate_code(value: str) -> str:
    """The pasted authorization code, stripped, or ``ValueError``.

    Google's codes are URL-safe text (``4/0Ab...``). The page at
    ``antigravity.google/oauth-callback`` shows it; an operator may paste the
    whole callback URL instead, so a ``code=`` parameter is extracted.
    """
    text = (value or "").strip()
    match = re.search(r"[?&]code=([^&\s]+)", text)
    if match:
        from urllib.parse import unquote

        text = unquote(match.group(1))
    if not _CODE_CHARS.match(text):
        raise ValueError(
            "That does not look like the authorization code Google showed "
            "(letters, digits and / _ - . ~, no spaces)."
        )
    return text


def parse_helper_line(line: str) -> dict[str, Any] | None:
    """A helper record from one output line, or ``None`` for anything else."""
    index = line.find(HELPER_PREFIX)
    if index < 0:
        return None
    try:
        record = json.loads(line[index + len(HELPER_PREFIX) :])
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None
