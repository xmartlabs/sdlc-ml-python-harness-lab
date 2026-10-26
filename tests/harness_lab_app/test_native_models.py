"""Native CLI model catalog (ADR-0069): one list, checked against the gateway."""

from __future__ import annotations

from harness_lab_app.native_models import (
    NATIVE_MODELS,
    gateway_coverage,
    native_catalog,
)


def test_the_current_generation_leads_and_is_ordered_smallest_first():
    claude = [m for m in NATIVE_MODELS["claude"] if m["generation"] == "current"]
    assert [m["id"] for m in claude[:3]] == [
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
    ]
    assert all(m["size_rank"] for m in NATIVE_MODELS["claude"])


def test_every_model_declares_what_the_ui_needs():
    for harness, models in NATIVE_MODELS.items():
        for model in models:
            assert set(model) >= {"id", "label", "generation", "size_rank"}, (
                harness,
                model,
            )


def test_coverage_marks_a_model_the_gateway_cannot_serve():
    routing = {"claude-opus-5-5": {}, "claude-haiku-4-5-20251001": {}}
    cov = gateway_coverage("claude", routing)
    assert cov["claude-opus-5-5"] is True
    assert cov["claude-sonnet-5-5"] is False


def test_coverage_accepts_the_dotted_and_oauth_spellings_a_gateway_uses():
    """LiteLLM cards are often named claude-haiku-4.5 or claude-opus-5-5-oauth."""
    routing = {"claude-haiku-4.5": {}, "claude-opus-5-5-oauth": {}}
    cov = gateway_coverage("claude", routing)
    assert cov["claude-haiku-4-5-20251001"] is True
    assert cov["claude-opus-5-5"] is True


def test_coverage_is_unknown_without_a_gateway():
    assert set(gateway_coverage("claude", None).values()) == {None}


def test_the_catalog_carries_coverage_per_model():
    catalog = native_catalog({"claude-opus-5-5": {}})
    claude = {m["id"]: m for m in catalog["claude"]}
    assert claude["claude-opus-5-5"]["on_gateway"] is True
    assert claude["claude-sonnet-5-5"]["on_gateway"] is False
    assert "codex" in catalog


def _endpoint(path: str):
    from harness_lab_app.api import create_app

    for route in create_app().router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"Route not found: {path}")


def test_the_endpoint_marks_models_the_gateway_does_not_serve(monkeypatch):
    from harness_lab_app import (
        api as api_module,
        gateway_models,
    )

    monkeypatch.setattr(
        api_module, "_gateway_endpoint", lambda: ("http://gw/v1", "tok")
    )
    monkeypatch.setattr(
        gateway_models,
        "fetch_gateway_routing_cached",
        lambda *_a, **_k: {"claude-opus-5-5-oauth": {}},
    )
    # This install's claude CLI goes through the gateway; not every one does.
    monkeypatch.setattr(
        "harness_lab_app.executors.claude_settings_env",
        lambda _root: {"ANTHROPIC_BASE_URL": "http://gw"},
    )
    claude = {m["id"]: m for m in _endpoint("/api/models/native")()["claude"]}
    assert claude["claude-opus-5-5"]["on_gateway"] is True
    assert claude["claude-sonnet-5-5"]["on_gateway"] is False


def test_the_endpoint_reports_unknown_without_a_gateway(monkeypatch):
    from harness_lab_app import api as api_module

    monkeypatch.setattr(api_module, "_gateway_endpoint", lambda: ("", ""))
    claude = _endpoint("/api/models/native")()["claude"]
    assert {m["on_gateway"] for m in claude} == {None}


def test_claude_coverage_is_unknown_when_claude_does_not_use_the_gateway():
    """A gateway for opencode says nothing about a claude CLI that talks to
    Anthropic directly: its models must not be marked 'not on your gateway'."""
    routing = {"claude-opus-5-5": {}}
    catalog = native_catalog(routing, claude_via_gateway=False)
    assert {m["on_gateway"] for m in catalog["claude"]} == {None}
    assert (
        native_catalog(routing, claude_via_gateway=True)["claude"][0]["on_gateway"]
        is False
    )
