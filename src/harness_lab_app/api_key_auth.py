"""API-key credentials for the Antigravity CLI harness (``antigravity_cli``).

Google documents two things for driving ``agy`` from automation
(antigravity.google/docs/faq, /docs/install): a consumer plan's sign-in must not
be wrapped by a third-party application, and a **Gemini API key** is the
supported credential for headless and CI runs — requests then go straight to
the Gemini API and "the CLI never establishes an account session". So this
harness never touches an OAuth session, a keychain or the operator's
``~/.gemini``: the only credential it knows is a key the operator pasted in the
UI.

This is deliberately not :mod:`harness_lab_app.native_auth`. That module keeps
codex/gemini on their *subscription* login and scrubs API keys out of their
environment; here the API key **is** the credential, so the rules are the
opposite and sharing code would blur exactly the line that matters (which
runs bill per token).

Where the key lives and who sees it:

* **Store:** ``<HARNESS_LAB_AUTH_DIR>/antigravity_cli/gemini_api_key`` (0600,
  directory 0700): outside the repository, outside ``data/runs``, never in
  ``.env`` (every run container receives ``.env``) and never in the database.
  Only the worker writes it; the API mounts the store read-only and only ever
  reports whether a key exists and its last four characters.
* **A run** gets the key as ``GEMINI_API_KEY`` in its container environment,
  plus a fresh ``HOME`` that is discarded afterwards, plus the CLI's settings
  passed with ``--settings`` (session-only, never written). Every other
  provider's credential is removed from that environment, so this harness
  never sees a key it does not need.
* **Known limit (verified on agy 1.5.14):** ``agy`` hands its whole
  environment to every command the agent runs, and offers no setting to scrub
  it; its ``--sandbox`` cannot start inside the runner container. Code the
  agent runs can therefore read the key. Runs on third-party code are refused
  unless the operator opts in (:data:`ALLOW_UNTRUSTED_ENV`), and every stream
  the run archives is scrubbed of the key's literal value
  (:func:`redact_secret`).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from harness_lab_app.native_auth import auth_root

#: The harness this module serves.
HARNESS = "antigravity_cli"

#: The CLI's own name for its credential (antigravity.google/docs/install):
#: "The CLI reads the credential only from GEMINI_API_KEY in the environment".
KEY_ENV = "GEMINI_API_KEY"

#: The settings ``agy`` needs to use the key, passed per session with
#: ``--settings`` so nothing is written into the run's home. ``gemini`` is the
#: only accepted value.
MODEL_PROVIDER_SETTINGS: dict[str, Any] = {"modelProvider": "gemini"}

#: Operator opt-in for runs on third-party code (SWE-bench instances, external
#: target repositories), where code the lab did not write could read the key.
ALLOW_UNTRUSTED_ENV = "HARNESS_LAB_AGY_ALLOW_UNTRUSTED"

#: ``agy``'s background self-updater would change the instrument mid-experiment
#: (troubleshooting docs: ``AGY_CLI_DISABLE_AUTO_UPDATE=true``).
DISABLE_AUTO_UPDATE = {"AGY_CLI_DISABLE_AUTO_UPDATE": "true"}

_KEY_FILE = "gemini_api_key"
_META_FILE = "meta.json"

# Gemini API keys from AI Studio are 39 characters starting with "AIza";
# Gemini Enterprise / Agent Platform keys differ, so only the alphabet and a
# sane length are enforced: no whitespace, no quotes, nothing a shell or a JSON
# document would reinterpret.
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-.]{20,256}$")

# Every variable that names a credential of *another* provider or service. An
# antigravity_cli container is handed none of them: the key it needs is the
# only secret in its environment. Matched by exact name and by the suffixes
# below, so a token added to `.env` later is dropped too.
_FOREIGN_SECRETS: frozenset[str] = frozenset(
    {
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_GENAI_USE_GCA",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_ACCESS_TOKEN",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_QUOTA_PROJECT",
        "GOOGLE_GEMINI_BASE_URL",
        "AGY_ADC_AUTH",
        "GEMINI_CLI_HOME",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GATEWAY_TOKEN",
        "LITELLM_MASTER_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "NVIDIA_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HARNESS_LAB_SESSION_SECRET",
        "HARNESS_LAB_ADMIN_PASSWORD_HASH",
        KEY_ENV,
    }
)
_SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_SECRET_KEY", "_PASSWORD")


def store_dir(root: Path | None = None) -> Path:
    """``<HARNESS_LAB_AUTH_DIR>/antigravity_cli``."""
    return (root or auth_root()) / HARNESS


def key_path(root: Path | None = None) -> Path:
    return store_dir(root) / _KEY_FILE


def validate_key(value: str) -> str:
    """The key, stripped, or ``ValueError`` naming what is wrong (never the key)."""
    key = (value or "").strip()
    if not key:
        raise ValueError("The API key is empty.")
    if not _KEY_RE.match(key):
        raise ValueError(
            "That does not look like a Gemini API key: 20-256 characters of "
            "letters, digits, '-', '_' or '.', with no spaces or quotes."
        )
    return key


def key_hint(key: str) -> str:
    """The last four characters, for the operator to recognise their key."""
    return f"…{key[-4:]}" if len(key) >= 8 else "…"


def save_key(value: str, root: Path | None = None) -> dict[str, Any]:
    """Write the key atomically (0600) and return its public status.

    Called on the worker only: the API mounts the store read-only.
    """
    key = validate_key(value)
    base = store_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    target = key_path(root)
    # A unique 0600 temporary (mkstemp), so two saves at once never share one
    # and the key is never readable by anyone else, not even for an instant.
    descriptor, temporary = tempfile.mkstemp(dir=base, prefix=".key-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(key)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    meta = {"saved_at": time.time(), "hint": key_hint(key)}
    (base / _META_FILE).write_text(json.dumps(meta), encoding="utf-8")
    return key_status(root)


def remove_key(root: Path | None = None) -> dict[str, Any]:
    """Forget the key (and its metadata). Never raises."""
    base = store_dir(root)
    for name in (_KEY_FILE, _META_FILE):
        (base / name).unlink(missing_ok=True)
    for leftover in base.glob(".key-*.tmp") if base.is_dir() else ():
        leftover.unlink(missing_ok=True)
    return key_status(root)


# --- handing a key from the API to the worker ---------------------------------
#
# The API cannot write the store (it is mounted read-only there), and a Celery
# message is the wrong carrier for a secret: `argsrepr` hides it from the
# normal task log, but a worker that does not know the task (an API restarted
# on new code before the worker) logs the whole body as "unregistered task".
# So the API drops the key into a 0600 file under the state directory both
# share, and the task carries only that file's random name. The worker moves
# the key into the store and deletes the drop. A drop nobody collects expires.

#: A drop older than this is deleted unread (the worker was down).
DROP_TTL_SECONDS = 15 * 60
_DROP_RE = re.compile(r"^key-drop-[0-9a-f]{32}$")


def write_drop(key: str, directory: Path) -> str:
    """Write ``key`` to a fresh 0600 drop file in ``directory``; its name."""
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    purge_drops(directory)
    name = f"key-drop-{secrets.token_hex(16)}"
    descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(validate_key(key))
    return name


def take_drop(name: str, directory: Path) -> str:
    """Read and delete one drop; ``""`` when it is missing, expired or not a drop.

    The name comes from a queue message, so it is matched against the exact
    shape :func:`write_drop` produces before any path is built from it.
    """
    if not _DROP_RE.match(name or ""):
        return ""
    path = directory / name
    try:
        if time.time() - path.stat().st_mtime > DROP_TTL_SECONDS:
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    finally:
        path.unlink(missing_ok=True)


def purge_drops(directory: Path) -> int:
    """Delete drops older than the TTL; how many. Never raises."""
    removed = 0
    if not directory.is_dir():
        return removed
    for path in directory.glob("key-drop-*"):
        try:
            if time.time() - path.stat().st_mtime > DROP_TTL_SECONDS:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


def read_key(root: Path | None = None) -> str:
    """The stored key, or ``""``. For the worker's run/test containers only."""
    try:
        return key_path(root).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def key_status(root: Path | None = None) -> dict[str, Any]:
    """Whether a key is configured, with its hint — never the key itself."""
    meta: dict[str, Any] = {}
    try:
        loaded = json.loads((store_dir(root) / _META_FILE).read_text("utf-8"))
        meta = loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        meta = {}
    configured = key_path(root).is_file()
    return {
        "configured": configured,
        "hint": meta.get("hint") if configured else None,
        "saved_at": meta.get("saved_at") if configured else None,
    }


def untrusted_allowed(env: dict[str, str] | None = None) -> bool:
    """True when the operator opted in to runs on third-party code."""
    values = env if env is not None else dict(os.environ)
    return str(values.get(ALLOW_UNTRUSTED_ENV, "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }


def is_foreign_secret(name: str) -> bool:
    """True for a variable that carries another provider's credential."""
    upper = name.upper()
    return upper in _FOREIGN_SECRETS or upper.endswith(_SECRET_SUFFIXES)


def agy_env(
    env: dict[str, str], *, key: str, home: str | Path | None = None
) -> dict[str, str]:
    """The environment an ``agy`` process runs under.

    ``env`` minus every foreign credential, plus the stored key, the
    self-update switch and (when given) a fresh ``HOME``. ``agy`` writes its
    conversations, logs, sqlite databases and builtin skills under
    ``$HOME/.gemini``, so a per-run home is what keeps one run's state out of
    the next run and out of every other arm.
    """
    out = {k: v for k, v in env.items() if not is_foreign_secret(k)}
    out.update(DISABLE_AUTO_UPDATE)
    if key:
        out[KEY_ENV] = key
    if home is not None:
        out["HOME"] = str(home)
    return out


def settings_json(extra: dict[str, Any] | None = None) -> str:
    """The ``--settings`` argument: the provider plus a run's own keys."""
    merged = {**MODEL_PROVIDER_SETTINGS, **(extra or {})}
    return json.dumps(merged, separators=(",", ":"))


def redact_secret(text: str, secret: str) -> tuple[str, int]:
    """``text`` with every occurrence of ``secret`` replaced, and the count.

    Exact-value matching only: the stream is scanned for the key the run was
    given, not for anything that looks like a key, so nothing else is altered.
    """
    if not secret or len(secret) < 8 or secret not in text:
        return text, 0
    return text.replace(secret, "***REDACTED***"), text.count(secret)


def redact_file(path: Path, secret: str) -> int:
    """Redact ``secret`` in one file in place; the number of hits. Never raises."""
    if not secret or len(secret) < 8:
        return 0
    try:
        raw = path.read_bytes()
    except OSError:
        return 0
    needle = secret.encode("utf-8")
    hits = raw.count(needle)
    if hits:
        try:
            path.write_bytes(raw.replace(needle, b"***REDACTED***"))
        except OSError:
            return 0
    return hits


def redact_tree(root: Path, secret: str, *, max_bytes: int = 50_000_000) -> int:
    """Redact ``secret`` in every regular file under ``root``; total hits.

    Used on a run directory after the container exits, before anything reads
    or exports it. Large binaries are skipped by size. Never raises.
    """
    if not secret or len(secret) < 8 or not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        total += redact_file(path, secret)
    return total


def fresh_home(parent: Path) -> Path:
    """A new, empty directory for one ``agy`` process's ``HOME`` (0700)."""
    if parent.exists():
        shutil.rmtree(parent, ignore_errors=True)
    parent.mkdir(parents=True)
    parent.chmod(0o700)
    return parent
