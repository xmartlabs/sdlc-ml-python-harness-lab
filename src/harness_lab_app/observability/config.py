"""Langfuse configuration, resolved from the environment.

Off is the default and the safe state: unless the master switch is explicitly
on *and* both API keys are present, :attr:`LangfuseConfig.enabled` is ``False``
and every caller degrades to a no-op with zero HTTP traffic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from harness_lab_app.config import read_env_file_value

# Truthy spellings accepted for the boolean switches. Anything else — including
# unset, "maybe", or an empty string — is false, so a fresh clone and CI stay
# silent without any configuration.
_TRUE = frozenset({"1", "true", "yes", "on"})

DEFAULT_HOST = "https://cloud.langfuse.com"
DEFAULT_ENVIRONMENT = "lab-local"
DEFAULT_SESSION_PREFIX = "bench-"

# OTLP ingestion path. Langfuse v4 rejects the old /api/public/ingestion
# endpoint for spans (400) and never had /api/public/traces (404); OTLP over
# HTTP is the supported route and accepts JSON as well as protobuf.
OTLP_TRACES_PATH = "/api/public/otel/v1/traces"


def _read(name: str, default: str = "") -> str:
    """`os.environ` first, then the repository `.env`.

    The run container gets its values through the environment
    (``executors.build_run_environment``); the API and worker read the same
    keys from `.env` via the one seam the test suite can redirect.
    """
    value = os.environ.get(name)
    if value is not None and value.strip():
        return value.strip()
    return read_env_file_value(name, default).strip()


def _flag(name: str, *, default: bool = False) -> bool:
    raw = _read(name)
    if not raw:
        return default
    return raw.lower() in _TRUE


@dataclass(frozen=True)
class LangfuseConfig:
    """Everything the exporter needs, already validated."""

    enabled: bool
    host: str
    public_key: str
    secret_key: str
    environment: str
    project_url: str
    session_prefix: str
    auto_export: bool
    # Why the integration is off, when it is off and the operator asked for it
    # to be on. Surfaced by the health check and the CLI; never raised.
    disabled_reason: str = ""
    # Whether LANGFUSE_ENABLED was on, regardless of whether the keys backed it
    # up. `requested and not enabled` is a misconfiguration worth warning about;
    # plain `not requested` is just an operator who never turned tracing on.
    requested: bool = False

    @property
    def traces_url(self) -> str:
        return f"{self.host.rstrip('/')}{OTLP_TRACES_PATH}"

    def session_id(self, run_id: str) -> str:
        """Deterministic session id for a run — one Langfuse session per run.

        Mirrors ``bench_run.resolve_or_synthesize_run``, which has written
        ``bench-<run_id>`` into `metadata.json` and `run.env` since long before
        anything read it back.
        """
        return f"{self.session_prefix}{run_id}"

    def session_url(self, run_id: str) -> str:
        """Deep link to the run's session, or "" when unknown.

        `LANGFUSE_PROJECT_URL` is the project root (the host cannot be turned
        into it: the project id only exists in the UI URL).
        """
        if not self.project_url:
            return ""
        return f"{self.project_url.rstrip('/')}/sessions/{self.session_id(run_id)}"

    @classmethod
    def from_env(cls) -> LangfuseConfig:
        requested = _flag("LANGFUSE_ENABLED", default=False)
        host = _read("LANGFUSE_HOST", DEFAULT_HOST)
        public_key = _read("LANGFUSE_PUBLIC_KEY")
        secret_key = _read("LANGFUSE_SECRET_KEY")

        reason = ""
        enabled = requested
        if requested:
            missing = [
                name
                for name, value in (
                    ("LANGFUSE_HOST", host),
                    ("LANGFUSE_PUBLIC_KEY", public_key),
                    ("LANGFUSE_SECRET_KEY", secret_key),
                )
                if not value
            ]
            if missing:
                enabled = False
                reason = (
                    "LANGFUSE_ENABLED is on but "
                    f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
                    "empty; tracing stays off."
                )
        else:
            reason = "LANGFUSE_ENABLED is not set to true."

        return cls(
            enabled=enabled,
            host=host,
            public_key=public_key,
            secret_key=secret_key,
            environment=_read("LANGFUSE_TRACING_ENVIRONMENT", DEFAULT_ENVIRONMENT),
            project_url=_read("LANGFUSE_PROJECT_URL"),
            session_prefix=_read("LANGFUSE_SESSION_PREFIX", DEFAULT_SESSION_PREFIX),
            auto_export=_flag("LANGFUSE_AUTO_EXPORT", default=True),
            disabled_reason=reason,
            requested=requested,
        )
