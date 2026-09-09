"""Per-call Langfuse tracing: one trace per in-process LLM call, wired through
`llm_transport.chat`.

Two properties carry the weight here, mirroring `test_observability_export.py`.
First, **a disabled Langfuse is a true no-op** — the transport is never
contacted, so a lab with tracing off pays nothing beyond the completion
itself. Second, **tracing can never change what `chat` returns or raises** — a
Langfuse outage (or an outright bug in the exporter) must cost observability
and nothing else.
"""

from __future__ import annotations

import json

import pytest

from harness_lab_app import llm_transport
from harness_lab_app.observability import call_export
from harness_lab_app.observability.config import LangfuseConfig

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
    for name in LANGFUSE_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def config(monkeypatch) -> LangfuseConfig:
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "unit-test")
    return LangfuseConfig.from_env()


class _ExplodingTransport:
    """Proves a code path made no HTTP call: reaching it is the failure."""

    def send(self, payload):
        raise AssertionError("Langfuse must not be contacted here")


class _CapturingTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, payload):
        from harness_lab_app.observability.client import SendResult

        self.sent.append(payload)
        return SendResult(ok=True, status=207)


# -- call_export.export_call --------------------------------------------------


def test_a_disabled_langfuse_traces_nothing():
    """An operator who never turned tracing on must pay zero network cost."""
    result = call_export.export_call(
        "repository_audit",
        model="gpt-5.4-mini",
        system="s",
        user="u",
        start_ns=0,
        end_ns=1,
        output="ok",
        transport=_ExplodingTransport(),
        config=LangfuseConfig.from_env(),
    )
    assert result.ok is False
    assert "LANGFUSE_ENABLED" in result.reason


def test_a_successful_call_produces_one_generation_span(config):
    transport = _CapturingTransport()
    result = call_export.export_call(
        "repository_audit",
        model="gpt-5.4-mini",
        system="You are an auditor.",
        user="Audit this repo.",
        start_ns=1_000,
        end_ns=2_000,
        output="Findings: none.",
        metadata={"target_id": "repo-1"},
        transport=transport,
        config=config,
    )
    assert result.ok is True
    spans = transport.sent[0]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 1
    span = spans[0]
    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert attrs["langfuse.observation.type"]["stringValue"] == "generation"
    assert attrs["langfuse.observation.model.name"]["stringValue"] == "gpt-5.4-mini"
    assert "Audit this repo." in attrs["langfuse.observation.input"]["stringValue"]
    assert attrs["langfuse.observation.output"]["stringValue"] == "Findings: none."
    assert attrs["langfuse.observation.metadata.target_id"]["stringValue"] == "repo-1"
    assert attrs["langfuse.trace.name"]["stringValue"] == "repository_audit"
    tags = attrs["langfuse.trace.tags"]["arrayValue"]["values"]
    assert {v["stringValue"] for v in tags} == {"llm-call", "repository_audit"}


def test_two_calls_of_the_same_kind_get_different_trace_ids(config):
    """Two audits of the same repository must not collapse into one trace."""
    first = call_export.export_call(
        "repository_audit",
        model="m",
        system="s",
        user="u",
        start_ns=0,
        end_ns=1,
        output="a",
        transport=_CapturingTransport(),
        config=config,
    )
    second = call_export.export_call(
        "repository_audit",
        model="m",
        system="s",
        user="u",
        start_ns=0,
        end_ns=1,
        output="a",
        transport=_CapturingTransport(),
        config=config,
    )
    assert first.trace_id != second.trace_id
    # Same kind still groups under one session, so they stay browsable together.
    assert first.session_id == second.session_id


def test_a_failed_call_is_traced_at_error_level_with_no_output(config):
    transport = _CapturingTransport()
    result = call_export.export_call(
        "judge",
        model="m",
        system="s",
        user="u",
        start_ns=0,
        end_ns=1,
        error="Gateway HTTP 500: boom",
        transport=transport,
        config=config,
    )
    assert result.ok is True
    span = transport.sent[0]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert attrs["langfuse.observation.level"]["stringValue"] == "ERROR"
    assert (
        attrs["langfuse.observation.status_message"]["stringValue"]
        == "Gateway HTTP 500: boom"
    )
    assert "langfuse.observation.output" not in attrs
    assert span["status"]["code"] == 2  # STATUS_ERROR


def test_secrets_in_the_prompt_are_redacted(config):
    transport = _CapturingTransport()
    call_export.export_call(
        "case_draft",
        model="m",
        system="s",
        user="Use GATEWAY_TOKEN=sk-live-shouldnotleak to authenticate.",
        start_ns=0,
        end_ns=1,
        output="ok",
        transport=transport,
        config=config,
    )
    body = json.dumps(transport.sent[0])
    assert "sk-live-shouldnotleak" not in body
    assert "GATEWAY_TOKEN=<redacted>" in body


def test_a_transport_error_is_reported_not_raised(config):
    class _Bomb:
        def send(self, payload):
            raise RuntimeError("connection refused")

    result = call_export.export_call(
        "judge",
        model="m",
        system="s",
        user="u",
        start_ns=0,
        end_ns=1,
        output="ok",
        transport=_Bomb(),
        config=config,
    )
    assert result.ok is False
    assert "connection refused" in result.reason


# -- llm_transport.chat -------------------------------------------------------


def test_chat_with_no_trace_kind_contacts_no_transport(monkeypatch):
    """Proves the default path costs nothing extra: `_trace_call` is never
    even invoked when `trace_kind` is left empty."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("tracing must not run when trace_kind is empty")

    monkeypatch.setattr(llm_transport, "_trace_call", _boom)
    monkeypatch.setattr(llm_transport, "available_transport", lambda: "gateway")
    monkeypatch.setattr(
        llm_transport, "gateway_config", lambda: ("http://gw.invalid/v1", "tok")
    )
    monkeypatch.setattr(llm_transport, "gateway_chat", lambda *_a, **_k: "an answer")
    assert llm_transport.chat("m", "s", "u") == "an answer"


def test_chat_with_trace_kind_but_langfuse_disabled_builds_nothing(monkeypatch):
    """Disabled is the default and safe state: even with `trace_kind` set, a
    lab that never turned Langfuse on must not build a payload or a span."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("must not export when Langfuse is disabled")

    monkeypatch.setattr("harness_lab_app.observability.call_export.export_call", _boom)
    monkeypatch.setattr(llm_transport, "available_transport", lambda: "gateway")
    monkeypatch.setattr(
        llm_transport, "gateway_config", lambda: ("http://gw.invalid/v1", "tok")
    )
    monkeypatch.setattr(llm_transport, "gateway_chat", lambda *_a, **_k: "an answer")

    assert llm_transport.chat("m", "s", "u", trace_kind="judge") == "an answer"


def test_a_tracing_transport_that_raises_does_not_break_chat(monkeypatch, config):
    """A broken exporter must cost observability, never the actual answer."""
    from harness_lab_app.observability.client import OtlpHttpTransport

    def _boom(self, payload, url=None):
        raise RuntimeError("Langfuse is unreachable")

    monkeypatch.setattr(OtlpHttpTransport, "send", _boom)
    monkeypatch.setattr(llm_transport, "available_transport", lambda: "gateway")
    monkeypatch.setattr(
        llm_transport, "gateway_config", lambda: ("http://gw.invalid/v1", "tok")
    )
    monkeypatch.setattr(llm_transport, "gateway_chat", lambda *_a, **_k: "the answer")

    text = llm_transport.chat("m", "s", "u", trace_kind="judge")

    assert text == "the answer"


def test_chat_emits_a_trace_when_trace_kind_is_set(monkeypatch, config):
    captured = {}

    def _fake_export_call(kind, **kwargs):
        captured["kind"] = kind
        captured["kwargs"] = kwargs
        from harness_lab_app.observability.call_export import CallExportResult

        return CallExportResult(ok=True, trace_id="abc", session_id="bench-judge")

    monkeypatch.setattr(
        "harness_lab_app.observability.call_export.export_call", _fake_export_call
    )
    monkeypatch.setattr(llm_transport, "available_transport", lambda: "gateway")
    monkeypatch.setattr(
        llm_transport, "gateway_config", lambda: ("http://gw.invalid/v1", "tok")
    )
    monkeypatch.setattr(llm_transport, "gateway_chat", lambda *_a, **_k: "the answer")

    text = llm_transport.chat(
        "m",
        "s",
        "u",
        trace_kind="judge",
        trace_metadata={"run_id": "run-1"},
    )

    assert text == "the answer"
    assert captured["kind"] == "judge"
    assert captured["kwargs"]["output"] == "the answer"
    assert captured["kwargs"]["error"] is None
    assert captured["kwargs"]["metadata"]["run_id"] == "run-1"
    assert captured["kwargs"]["metadata"]["transport"] == "gateway"


def test_chat_traces_a_failed_call_and_still_raises(monkeypatch, config):
    captured = {}

    def _fake_export_call(kind, **kwargs):
        captured["kind"] = kind
        captured["kwargs"] = kwargs
        from harness_lab_app.observability.call_export import CallExportResult

        return CallExportResult(ok=True)

    monkeypatch.setattr(
        "harness_lab_app.observability.call_export.export_call", _fake_export_call
    )
    monkeypatch.setattr(llm_transport, "available_transport", lambda: "gateway")
    monkeypatch.setattr(
        llm_transport, "gateway_config", lambda: ("http://gw.invalid/v1", "tok")
    )

    def _raise(*_a, **_k):
        raise llm_transport.GatewayHTTPError(500, "boom")

    monkeypatch.setattr(llm_transport, "gateway_chat", _raise)

    with pytest.raises(llm_transport.GatewayHTTPError):
        llm_transport.chat("m", "s", "u", trace_kind="repository_audit")

    assert captured["kind"] == "repository_audit"
    assert captured["kwargs"]["output"] is None
    assert "boom" in captured["kwargs"]["error"]
