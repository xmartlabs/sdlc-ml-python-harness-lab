"""Allowlisted view over the repository ``.env`` for the Settings page.

Only keys declared in :data:`EDITABLE_KEYS` can be written. Two categories are
deliberately excluded:

``HARNESS_LAB_*``
    ``config.get_settings()`` is ``lru_cache``d, so these only take effect on
    restart; and ``REPO_ROOT`` / ``DATABASE_URL`` / ``EXECUTION_BACKEND`` would
    break the running containers if changed underneath them. They are exposed
    read-only as runtime facts instead.

Authentication material
    ``HARNESS_LAB_ADMIN_PASSWORD_HASH`` and ``HARNESS_LAB_SESSION_SECRET`` are
    written only by the password-change endpoint, which re-verifies the current
    password first.

Secret values never leave the process: reads return a mask, and writes accept
:data:`UNCHANGED_SENTINEL` to mean "keep what is stored".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_lab_app.config import get_settings
from harness_lab_app.env_file import (
    BACKUP_DIRNAME,
    EnvFileError,
    duplicate_keys,
    read_all,
    validate_assignment,
    values,
    write_env,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

UNCHANGED_SENTINEL = "••••••"

GROUP_GATEWAY = "gateway"
GROUP_MODELS = "models"
GROUP_RUNTIMES = "runtimes"
GROUP_CREDENTIALS = "credentials"
GROUP_TRACING = "tracing"

_URL_RE = re.compile(r"^https?://\S+$")


class SettingsError(ValueError):
    """Raised when a settings write is rejected."""


@dataclass(frozen=True)
class EnvKeySpec:
    key: str
    label: str
    group: str
    is_secret: bool = False
    help_text: str = ""
    validator: Callable[[str], None] | None = None


def _require_url(value: str) -> None:
    if value and not _URL_RE.match(value):
        raise SettingsError(f"Expected an http(s) URL, got {value!r}.")


def _require_gateway_url(value: str) -> None:
    _require_url(value)
    if value and not value.rstrip("/").endswith("/v1"):
        raise SettingsError(
            "The LiteLLM base URL must end in /v1 (for example "
            "http://localhost:4000/v1)."
        )


def _require_model_id(value: str) -> None:
    if value and "/" not in value:
        raise SettingsError(
            f"Expected a provider-qualified model id such as 'gateway/{value}'."
        )


_SPECS: tuple[EnvKeySpec, ...] = (
    EnvKeySpec(
        key="GATEWAY_BASE_URL",
        label="Gateway base URL",
        group=GROUP_GATEWAY,
        help_text="LiteLLM OpenAI-compatible endpoint, ending in /v1.",
        validator=_require_gateway_url,
    ),
    EnvKeySpec(
        key="GATEWAY_TOKEN",
        label="Gateway token",
        group=GROUP_GATEWAY,
        is_secret=True,
        help_text="Virtual key used by runs and in-process LLM calls.",
    ),
    EnvKeySpec(
        key="LITELLM_MASTER_KEY",
        label="LiteLLM master key",
        group=GROUP_GATEWAY,
        is_secret=True,
        help_text="Only needed to mint per-run virtual keys from the CLI lane.",
    ),
    EnvKeySpec(
        key="OPENCODE_MODEL",
        label="Executor model",
        group=GROUP_MODELS,
        validator=_require_model_id,
    ),
    EnvKeySpec(
        key="OPENCODE_MODEL_PLAN",
        label="Planner model",
        group=GROUP_MODELS,
        validator=_require_model_id,
    ),
    EnvKeySpec(
        key="OPENCODE_SMALL_MODEL",
        label="Small/utility model",
        group=GROUP_MODELS,
        validator=_require_model_id,
    ),
    EnvKeySpec(key="AUDITOR_MODEL", label="Audit model", group=GROUP_MODELS),
    EnvKeySpec(key="IMPROVE_MODEL", label="Improvement model", group=GROUP_MODELS),
    EnvKeySpec(
        key="CASE_SUMMARY_MODEL", label="Case summary model", group=GROUP_MODELS
    ),
    EnvKeySpec(
        key="LMSTUDIO_BASE_URL",
        label="LM Studio base URL",
        group=GROUP_RUNTIMES,
        validator=_require_url,
        help_text=(
            "Server address including /v1 (e.g. http://192.168.0.223:1234/v1). "
            "Feeds the 'LM Studio (direct, no gateway)' model group, which "
            "reads models off this host by name — no alias in between."
        ),
    ),
    EnvKeySpec(
        key="OLLAMA_BASE_URL",
        label="Ollama base URL",
        group=GROUP_RUNTIMES,
        validator=_require_url,
    ),
    EnvKeySpec(
        key="CLAUDE_CODE_OAUTH_TOKEN",
        label="Claude Code OAuth token",
        group=GROUP_CREDENTIALS,
        is_secret=True,
        help_text="Account-based credential for the claude harness lane.",
    ),
    EnvKeySpec(
        key="ANTHROPIC_API_KEY",
        label="Anthropic API key",
        group=GROUP_CREDENTIALS,
        is_secret=True,
    ),
    EnvKeySpec(
        key="NVIDIA_API_KEY",
        label="NVIDIA API key",
        group=GROUP_CREDENTIALS,
        is_secret=True,
    ),
    EnvKeySpec(
        key="LANGFUSE_HOST",
        label="Langfuse host",
        group=GROUP_TRACING,
        validator=_require_url,
    ),
    EnvKeySpec(
        key="LANGFUSE_PUBLIC_KEY", label="Langfuse public key", group=GROUP_TRACING
    ),
    EnvKeySpec(
        key="LANGFUSE_SECRET_KEY",
        label="Langfuse secret key",
        group=GROUP_TRACING,
        is_secret=True,
    ),
)

EDITABLE_KEYS: dict[str, EnvKeySpec] = {spec.key: spec for spec in _SPECS}

# Written only via the password-change endpoint, never the generic editor.
PROTECTED_KEYS = frozenset(
    {"HARNESS_LAB_ADMIN_PASSWORD_HASH", "HARNESS_LAB_SESSION_SECRET"}
)


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) < 8:
        return "••••"
    return f"{value[:3]}…{value[-4:]}"


def _env_path() -> Path:
    return get_settings().repo_root / ".env"


def _backup_dir() -> Path:
    return get_settings().repo_root / "data" / "app" / BACKUP_DIRNAME


def read_settings() -> dict:
    """Grouped, masked view of every editable key plus duplicate warnings."""
    path = _env_path()
    stored = values(path)
    warnings = [
        f"{key} is assigned more than once in .env; the last value wins on write."
        for key in duplicate_keys(read_all(path))
    ]

    items = []
    for spec in _SPECS:
        raw = stored.get(spec.key, "")
        items.append(
            {
                "key": spec.key,
                "label": spec.label,
                "group": spec.group,
                "is_secret": spec.is_secret,
                "help_text": spec.help_text,
                "is_set": bool(raw),
                "value": mask_secret(raw) if spec.is_secret else raw,
            }
        )
    return {"items": items, "warnings": warnings, "env_path": str(path)}


def read_runtime_facts() -> dict:
    """Read-only settings surfaced as facts (changing them needs a restart)."""
    settings = get_settings()
    return {
        "repo_root": str(settings.repo_root),
        "database_url": settings.database_url,
        "execution_backend": settings.execution_backend,
        "runner_image": settings.runner_image,
        "sandbox_image": settings.sandbox_image,
        "docker_host_alias": settings.docker_host_alias,
        "redis_url": settings.redis_url,
        "run_timeout_seconds": settings.run_timeout_seconds,
        "run_stall_seconds": settings.run_stall_seconds,
        "run_model_wait_seconds": settings.run_model_wait_seconds,
        "run_max_stream_errors": settings.run_max_stream_errors,
    }


def apply_settings(updates: dict[str, str]) -> dict:
    """Validate and persist ``updates``, skipping unchanged secrets."""
    rejected = sorted(set(updates) - set(EDITABLE_KEYS))
    if rejected:
        raise SettingsError(
            "These keys are not editable from the web: " + ", ".join(rejected)
        )

    effective: dict[str, str] = {}
    for key, value in updates.items():
        spec = EDITABLE_KEYS[key]
        if spec.is_secret and value == UNCHANGED_SENTINEL:
            continue
        cleaned = value.strip()
        if spec.validator is not None:
            spec.validator(cleaned)
        try:
            validate_assignment(key, cleaned)
        except EnvFileError as exc:
            raise SettingsError(str(exc)) from exc
        effective[key] = cleaned

    if not effective:
        return {"written": [], "backup": None}

    backup = write_env(_env_path(), effective, _backup_dir())
    return {
        "written": sorted(effective),
        "backup": str(backup) if backup else None,
    }
