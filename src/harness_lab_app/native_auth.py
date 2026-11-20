"""Subscription sessions for the native agent CLIs, kept apart per provider.

The ``codex`` and ``gemini`` harnesses run the official CLI with the login the
CLI itself performs — ChatGPT sign-in for Codex (``codex login
--device-auth``), Sign in with Google for Gemini CLI. The lab never reads a
token's value and never calls a provider API with one; it only decides **which
directory** each CLI keeps its session in, and **which environment** it runs
under, so that:

* a run spends the plan's quota and not a per-token API key that happens to be
  in ``.env`` (Codex honours ``OPENAI_API_KEY``/``CODEX_API_KEY``; Gemini
  resolves ``GEMINI_API_KEY`` when ``GOOGLE_GENAI_USE_GCA`` is unset);
* a run sees none of the operator's personal configuration. ``~/.codex`` holds
  personal ``skills/``, ``memories/``, an ``AGENTS.md`` and plugins, and Codex
  loads user-scope skills from ``$CODEX_HOME/skills`` and ``$HOME/.agents/
  skills`` (verified with ``codex debug prompt-input``, codex-cli 0.171.0).
  Pointing a run at it would hand every arm — ``bare`` included — the
  operator's own harness;
* a Docker run mounts only its own provider's session, never the other one's.

Layout (``HARNESS_LAB_AUTH_DIR``, default ``~/.harness-lab/auth``, outside the
repository and outside ``data/runs``)::

    <auth>/codex/            ← CODEX_HOME: auth.json + the lab's config.toml
    <auth>/gemini/.gemini/   ← GEMINI_CLI_HOME/.gemini: oauth_creds.json + settings.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

NativeHarness = Literal["codex", "gemini"]

#: The harnesses whose credentials live in a lab-managed session directory.
NATIVE_SESSION_HARNESSES: tuple[NativeHarness, ...] = ("codex", "gemini")

#: Where a provider's session directory is mounted inside a runner container.
CONTAINER_AUTH_ROOT = "/run/harness-auth"
#: An empty home for codex/gemini runs, so no user-scope skill or config leaks.
CONTAINER_AGENT_HOME = "/tmp/agent-home"  # nosec B108 - container-local path

DEFAULT_AUTH_DIR = Path("~/.harness-lab/auth")

# Settings the lab pins for every run. Codex: no update check, no analytics,
# no cross-session memory (a memory written by arm A must not reach arm B).
_CODEX_CONFIG = """\
# Written by the harness lab (harness_lab_app.native_auth). A run reads this
# CODEX_HOME and nothing else, so the operator's own ~/.codex never leaks in.
check_for_update_on_startup = false
cli_auth_credentials_store = "file"

[analytics]
enabled = false

[features]
memories = false
"""

# Gemini: Sign in with Google, no self-update mid-experiment, no usage stats.
_GEMINI_SETTINGS: dict[str, Any] = {
    # No tool exclusion for memory: gemini-cli 0.70 has no `save_memory` tool,
    # it writes memories with `write_file`/`replace` into `~/.gemini/GEMINI.md`
    # (docs/tools/memory.md). What keeps such a memory out of the next run is
    # the per-run private copy of the home (stage_run_session), not a setting.
    "security": {"auth": {"selectedType": "oauth-personal"}},
    "general": {"enableAutoUpdate": False, "enableAutoUpdateNotification": False},
    "privacy": {"usageStatisticsEnabled": False},
}

# Env vars that would switch a CLI from its subscription login to per-token
# billing (or to another provider entirely). Removed from a codex/gemini run's
# environment; the session directory is the only credential it gets.
_BILLING_OVERRIDES: tuple[str, ...] = (
    # OpenAI / Codex
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    # Google / Gemini
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GEMINI_BASE_URL",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_ACCESS_TOKEN",
    "GEMINI_FORCE_ENCRYPTED_FILE_STORAGE",
    # Another provider's credentials have no business in this run.
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
)


def auth_root() -> Path:
    """The per-provider session store (``HARNESS_LAB_AUTH_DIR``)."""
    configured = os.environ.get("HARNESS_LAB_AUTH_DIR", "").strip()
    return (
        Path(configured).expanduser() if configured else DEFAULT_AUTH_DIR.expanduser()
    )


def session_dir(harness: str, root: Path | None = None) -> Path:
    """The directory one provider's CLI keeps its session in."""
    return (root or auth_root()) / harness


def credential_file(harness: str, root: Path | None = None) -> Path:
    """The file the official CLI writes after a successful login."""
    base = session_dir(harness, root)
    if harness == "codex":
        return base / "auth.json"
    return base / ".gemini" / "oauth_creds.json"


def ensure_session_dir(harness: str, root: Path | None = None) -> Path:
    """Create the provider directory (0700) with the lab's pinned settings.

    Existing settings are left alone except for the keys the lab pins, so an
    operator's manual tweak to an unrelated key survives.
    """
    base = session_dir(harness, root)
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    if harness == "codex":
        config = base / "config.toml"
        if not config.exists():
            config.write_text(_CODEX_CONFIG, encoding="utf-8")
        return base
    gemini_dir = base / ".gemini"
    gemini_dir.mkdir(exist_ok=True)
    gemini_dir.chmod(0o700)
    settings_path = gemini_dir / "settings.json"
    current: dict[str, Any] = {}
    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            current = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            current = {}
    merged = _deep_merge(current, _GEMINI_SETTINGS)
    if merged != current:
        settings_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return base


def _deep_merge(base: dict[str, Any], pinned: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in pinned.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def session_status(harness: str, root: Path | None = None) -> dict[str, Any]:
    """Whether a provider session exists, read from its *shape*, never its value.

    ``connected`` means the official CLI wrote its credential file with the
    fields a subscription login produces; ``missing`` means no file. Expiry is
    not decided here: a refresh token outlives the access token, and only the
    CLI can tell whether a refresh still succeeds — a run that finds it expired
    fails with the CLI's own message (codex ``turn.failed`` 401, gemini exit 41).
    """
    path = credential_file(harness, root)
    status: dict[str, Any] = {
        "harness": harness,
        "session_dir": str(session_dir(harness, root)),
        "state": "missing",
        "auth_kind": None,
    }
    if not path.is_file():
        return status
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        status["state"] = "unreadable"
        return status
    if not isinstance(data, dict):
        status["state"] = "unreadable"
        return status
    if harness == "codex":
        tokens = data.get("tokens")
        has_tokens = isinstance(tokens, dict) and bool(tokens.get("refresh_token"))
        # `auth_mode` is what `codex login status` reports ("chatgpt" for a
        # ChatGPT plan; an API key login stores OPENAI_API_KEY instead).
        mode = str(data.get("auth_mode") or "")
        if data.get("OPENAI_API_KEY"):
            status.update(state="api_key", auth_kind="api_key")
        elif has_tokens:
            status.update(state="connected", auth_kind=mode or "chatgpt")
        else:
            status["state"] = "incomplete"
        return status
    if data.get("refresh_token") or data.get("access_token"):
        status.update(state="connected", auth_kind="oauth-personal")
    else:
        status["state"] = "incomplete"
    return status


# What a run's private copy of a session may contain: the credential the
# official CLI wrote, and the lab's pinned config. Nothing else crosses into a
# run, and only the credential crosses back.
_SESSION_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "codex": ("auth.json", "config.toml"),
    "gemini": (".gemini/oauth_creds.json", ".gemini/settings.json"),
}


def _credential_relative(harness: str) -> str:
    return "auth.json" if harness == "codex" else ".gemini/oauth_creds.json"


def prune_session_dir(harness: str, root: Path | None = None) -> list[str]:
    """Remove everything but the allow-listed files from a session directory.

    For the host lane, where the CLI uses the store in place. Returns the
    relative paths removed (for the run log). Never raises.
    """
    import shutil

    base = session_dir(harness, root)
    keep = set(_SESSION_ALLOWLIST[harness])
    keep_dirs = {str(Path(rel).parent) for rel in keep} - {"."}
    removed: list[str] = []
    if not base.is_dir():
        return removed
    for path in sorted(base.rglob("*"), reverse=True):
        relative = str(path.relative_to(base))
        if relative in keep or relative in keep_dirs:
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(relative)
        except OSError:
            continue
    return removed


def stage_run_session(
    harness: str, destination: Path, root: Path | None = None
) -> Path:
    """Copy the allow-listed session files into a run's private directory.

    The store is shared by every run and every arm; the CLI's home is not just
    a credential. Gemini's ``save_memory`` appends to ``$GEMINI_CLI_HOME/
    .gemini/GEMINI.md`` and a codex agent can write ``$CODEX_HOME/AGENTS.md`` or
    ``skills/`` — files the next run, of any arm, would load. A run therefore
    gets a fresh copy holding only the credential and the lab's config; whatever
    it writes there is discarded with the run (:func:`return_run_session`).
    """
    source = session_dir(harness, root)
    if destination.exists():
        import shutil

        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    destination.chmod(0o700)
    for relative in _SESSION_ALLOWLIST[harness]:
        origin = source / relative
        if not origin.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.parent.chmod(0o700)
        target.write_bytes(origin.read_bytes())
        target.chmod(0o600)
    return destination


def return_run_session(harness: str, staged: Path, root: Path | None = None) -> bool:
    """Write a refreshed credential back to the store, then drop the run's copy.

    Only the credential file returns, and only when it is still a well-formed
    login of the same kind (a run cannot plant an API key, or garbage, in the
    store). Returns True when the store was updated. Never raises.
    """
    import shutil

    updated = False
    try:
        relative = _credential_relative(harness)
        fresh = staged / relative
        stored = session_dir(harness, root) / relative
        changed = fresh.is_file() and (
            not stored.is_file() or fresh.read_bytes() != stored.read_bytes()
        )
        if changed and _status_of(harness, fresh) == "connected":
            temporary = stored.with_suffix(stored.suffix + ".tmp")
            temporary.write_bytes(fresh.read_bytes())
            temporary.chmod(0o600)
            temporary.replace(stored)
            updated = True
    except OSError:
        updated = False
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    return updated


def _status_of(harness: str, path: Path) -> str:
    """:func:`session_status`'s verdict for one credential file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unreadable"
    if not isinstance(data, dict):
        return "unreadable"
    if harness == "codex":
        if data.get("OPENAI_API_KEY"):
            return "api_key"
        tokens = data.get("tokens")
        ok = isinstance(tokens, dict) and bool(tokens.get("refresh_token"))
        return "connected" if ok else "incomplete"
    ok = bool(data.get("refresh_token") or data.get("access_token"))
    return "connected" if ok else "incomplete"


def billing_mode(harness: str, env: dict[str, str] | None = None) -> str:
    """How a run of ``harness`` is paid for: ``subscription`` | ``api`` | ``gateway`` | ``unknown``.

    codex/gemini always run on their session directory (the env is scrubbed),
    so they are ``subscription``; claude depends on the credential it was
    given. opencode routes through the gateway or a self-hosted model.
    """
    if harness in NATIVE_SESSION_HARNESSES:
        return "subscription"
    if harness == "claude":
        values = env if env is not None else dict(os.environ)
        if values.get("ANTHROPIC_BASE_URL"):
            return "gateway"
        if values.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return "subscription"
        if values.get("ANTHROPIC_API_KEY"):
            return "api"
        return "unknown"
    if harness == "opencode":
        return "gateway"
    return "unknown"


def scrub_billing_overrides(env: dict[str, str]) -> dict[str, str]:
    """A copy of ``env`` without any variable that would bypass the session."""
    return {k: v for k, v in env.items() if k not in _BILLING_OVERRIDES}


def harness_env(
    harness: str,
    env: dict[str, str],
    *,
    session_root: str | Path,
    home: str | Path | None = None,
) -> dict[str, str]:
    """The environment a codex/gemini CLI runs under (other harnesses: unchanged).

    ``session_root`` is the provider's session directory *as the CLI sees it*
    (a host path on the host lane, ``/run/harness-auth/<provider>`` in Docker).
    ``home`` replaces ``HOME`` so no user-scope skill directory is found; the
    session directory itself is addressed through ``CODEX_HOME`` /
    ``GEMINI_CLI_HOME``, which both CLIs honour ahead of ``HOME``.
    """
    if harness not in NATIVE_SESSION_HARNESSES:
        return dict(env)
    out = scrub_billing_overrides(env)
    if home is not None:
        out["HOME"] = str(home)
    if harness == "codex":
        out["CODEX_HOME"] = str(session_root)
        return out
    out["GEMINI_CLI_HOME"] = str(session_root)
    # Sign in with Google, resolved ahead of any API key (getAuthTypeFromEnv).
    out["GOOGLE_GENAI_USE_GCA"] = "true"
    # A headless run in an untrusted folder exits FatalUntrustedWorkspaceError.
    out["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    out["NO_BROWSER"] = "true"
    return out
