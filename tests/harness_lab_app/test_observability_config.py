"""Config, transport selection and redaction — the off switch above all."""

from __future__ import annotations

import pytest

from harness_lab_app.observability.client import (
    NullTransport,
    OtlpHttpTransport,
    get_transport,
)
from harness_lab_app.observability.config import LangfuseConfig
from harness_lab_app.observability.redaction import redact

LANGFUSE_VARS = (
    "LANGFUSE_ENABLED",
    "LANGFUSE_HOST",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_TRACING_ENVIRONMENT",
    "LANGFUSE_PROJECT_URL",
    "LANGFUSE_SESSION_PREFIX",
    "LANGFUSE_AUTO_EXPORT",
)


@pytest.fixture(autouse=True)
def _clean_langfuse_env(monkeypatch):
    """The developer's own shell must not decide what these tests assert.

    Their `.env` cannot either: `conftest._isolated_env_file` already points
    every reader at a fixture file with no Langfuse keys in it.
    """
    for name in LANGFUSE_VARS:
        monkeypatch.delenv(name, raising=False)


def _enable(monkeypatch, **overrides: str) -> LangfuseConfig:
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    return LangfuseConfig.from_env()


def test_disabled_when_unset():
    config = LangfuseConfig.from_env()
    assert config.enabled is False
    assert "LANGFUSE_ENABLED" in config.disabled_reason


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "", "maybe"])
def test_only_explicit_truthy_values_enable(monkeypatch, raw):
    monkeypatch.setenv("LANGFUSE_ENABLED", raw)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    assert LangfuseConfig.from_env().enabled is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_spellings_enable(monkeypatch, raw):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_ENABLED", raw)
    assert LangfuseConfig.from_env().enabled is True


def test_enabled_without_keys_degrades_to_off(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    config = LangfuseConfig.from_env()
    assert config.enabled is False
    assert "LANGFUSE_PUBLIC_KEY" in config.disabled_reason
    assert "LANGFUSE_SECRET_KEY" in config.disabled_reason


def test_urls_and_session_id(monkeypatch):
    config = _enable(
        monkeypatch,
        LANGFUSE_HOST="https://lf.example.com/",
        LANGFUSE_PROJECT_URL="https://lf.example.com/project/abc",
    )
    assert config.traces_url == "https://lf.example.com/api/public/otel/v1/traces"
    assert config.session_id("tetris-v1-K-061") == "bench-tetris-v1-K-061"
    assert config.session_url("tetris-v1-K-061").endswith(
        "/project/abc/sessions/bench-tetris-v1-K-061"
    )


def test_session_url_empty_without_project_url(monkeypatch):
    assert _enable(monkeypatch).session_url("run-1") == ""


def test_session_prefix_is_configurable(monkeypatch):
    config = _enable(monkeypatch, LANGFUSE_SESSION_PREFIX="lab-")
    assert config.session_id("run-1") == "lab-run-1"


def test_disabled_config_yields_a_transport_that_never_calls_out():
    transport = get_transport(LangfuseConfig.from_env())
    assert isinstance(transport, NullTransport)
    result = transport.send({"resourceSpans": []})
    assert result.ok is False


def test_enabled_config_yields_the_http_transport(monkeypatch):
    assert isinstance(get_transport(_enable(monkeypatch)), OtlpHttpTransport)


def test_transport_sends_basic_auth_and_the_v4_header(monkeypatch):
    captured: dict[str, object] = {}

    class _Response:
        status = 207

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def _opener(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["body"] = request.data
        return _Response()

    config = _enable(monkeypatch, LANGFUSE_HOST="https://lf.example.com")
    result = OtlpHttpTransport(config, opener=_opener).send({"resourceSpans": []})

    assert result.ok is True
    assert captured["url"] == "https://lf.example.com/api/public/otel/v1/traces"
    headers = captured["headers"]
    assert headers["Authorization"].startswith("Basic ")
    assert headers["X-langfuse-ingestion-version"] == "4"
    assert headers["Content-type"] == "application/json"


def test_transport_fails_open_on_a_network_error(monkeypatch):
    def _boom(request, timeout):
        raise OSError("connection refused")

    result = OtlpHttpTransport(_enable(monkeypatch), opener=_boom).send({})
    assert result.ok is False
    assert "connection refused" in result.reason


def test_redact_masks_credentials_but_keeps_context():
    text = (
        "GATEWAY_TOKEN=sk-live-abcdefghijklmnop\n"
        "Authorization: Bearer abcdefghijklmnopqrstuvwx\n"
        "workspace at /Users/dev/lab and http://localhost:4000\n"
    )
    masked = redact(text)
    assert "sk-live-abcdefghijklmnop" not in masked
    assert "abcdefghijklmnopqrstuvwx" not in masked
    assert "GATEWAY_TOKEN=<redacted>" in masked
    # Paths and local endpoints are what makes a trace useful to debug with.
    assert "/Users/dev/lab" in masked
    assert "localhost:4000" in masked


def test_redact_is_a_noop_on_clean_text():
    assert redact("step 3 finished, 4 files changed") == (
        "step 3 finished, 4 files changed"
    )
