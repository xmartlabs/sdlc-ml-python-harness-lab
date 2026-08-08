from __future__ import annotations

import json
import sys
from pathlib import Path

from harness_lab_app import gateway_models
from harness_lab_app.gateway_models import (
    DEFAULT_EXECUTOR_MODEL,
    build_model_catalog,
    gateway_model_id,
    normalize_model_name,
    read_opencode_gateway_models,
)

OPENCODE = {
    "gpt-5.5": {"name": "GPT-5.5 (cloud)"},
    "lmstudio-qwen3.6-35b-build": {"name": "Qwen3.6 35B build"},
}


def test_prefix_roundtrip_and_passthrough():
    assert gateway_model_id("gpt-5.5") == "gateway/gpt-5.5"
    assert normalize_model_name("gateway/gpt-5.5") == "gpt-5.5"
    # Already-prefixed ids and other providers are left untouched.
    assert gateway_model_id("openai/gpt-4o-mini") == "openai/gpt-4o-mini"
    assert normalize_model_name("openai/gpt-4o-mini") == "openai/gpt-4o-mini"


def test_catalog_prefers_live_list_and_labels_from_opencode():
    catalog = build_model_catalog(["gpt-5.5", "sol-preview"], OPENCODE)
    assert catalog["source"] == "gateway"
    ids = [m["id"] for m in catalog["models"]]
    assert ids == ["gateway/gpt-5.5", "gateway/sol-preview"]
    labels = {m["id"]: m["label"] for m in catalog["models"]}
    # Known id gets the opencode label; unknown id falls back to the bare id.
    assert labels["gateway/gpt-5.5"] == "GPT-5.5 (cloud)"
    assert labels["gateway/sol-preview"] == "sol-preview"


def test_catalog_flags_models_no_source_can_route():
    """The trap the model catalog exists to close.

    ``sol-preview`` is served by the gateway but declared nowhere, so opencode
    cannot resolve it — the run form must be able to say so before launch.
    """
    catalog = build_model_catalog(["gpt-5.5", "sol-preview"], OPENCODE)
    routable = {m["id"]: m["routable"] for m in catalog["models"]}
    assert routable == {"gateway/gpt-5.5": True, "gateway/sol-preview": False}


def test_the_overlay_makes_a_model_routable_and_names_it():
    overlay = {"sol-preview": {"name": "Sol preview (web)"}}
    catalog = build_model_catalog(["sol-preview"], OPENCODE, overlay=overlay)
    row = catalog["models"][0]
    assert row["routable"] is True
    assert row["label"] == "Sol preview (web)"


def test_the_offline_fallback_includes_overlay_ids():
    catalog = build_model_catalog([], OPENCODE, overlay={"only-in-overlay": {}})
    assert catalog["source"] == "opencode.json"
    assert "gateway/only-in-overlay" in {m["id"] for m in catalog["models"]}


def test_the_model_cache_serves_repeat_reads_and_clears_on_demand(monkeypatch):
    calls = []

    def _fake(base_url: str, token: str) -> list[str]:
        calls.append((base_url, token))
        return ["gpt-5.5"]

    monkeypatch.setattr(gateway_models, "list_gateway_models", _fake)
    gateway_models.clear_model_cache()

    assert gateway_models.list_gateway_models_cached("https://gw/v1", "t") == [
        "gpt-5.5"
    ]
    assert gateway_models.list_gateway_models_cached("https://gw/v1", "t") == [
        "gpt-5.5"
    ]
    assert len(calls) == 1

    # The Refresh button must always hit the gateway.
    gateway_models.clear_model_cache()
    gateway_models.list_gateway_models_cached("https://gw/v1", "t")
    assert len(calls) == 2


def test_an_empty_result_is_never_cached(monkeypatch):
    """A momentarily unreachable gateway must not stick for 30 seconds."""
    calls = []
    monkeypatch.setattr(
        gateway_models,
        "list_gateway_models",
        lambda **_kw: calls.append(1) or [],
    )
    gateway_models.clear_model_cache()

    gateway_models.list_gateway_models_cached("https://gw/v1", "t")
    gateway_models.list_gateway_models_cached("https://gw/v1", "t")
    assert len(calls) == 2


def test_catalog_default_executor_preserved_or_falls_back():
    live = ["lmstudio-qwen3.6-35b-build", "gpt-5.5"]
    assert (
        build_model_catalog(live, OPENCODE)["default_executor"]
        == DEFAULT_EXECUTOR_MODEL
    )
    # When the preferred default is not served, fall back to the first id.
    assert (
        build_model_catalog(["gpt-5.5"], OPENCODE)["default_executor"]
        == "gateway/gpt-5.5"
    )
    assert build_model_catalog([], {})["default_executor"] == ""


def test_catalog_falls_back_to_opencode_when_gateway_empty():
    catalog = build_model_catalog([], OPENCODE)
    assert catalog["source"] == "opencode.json"
    assert {m["id"] for m in catalog["models"]} == {
        "gateway/gpt-5.5",
        "gateway/lmstudio-qwen3.6-35b-build",
    }


def test_read_opencode_gateway_models(tmp_path: Path):
    (tmp_path / "opencode.json").write_text(
        json.dumps({"provider": {"gateway": {"models": OPENCODE}}})
    )
    assert read_opencode_gateway_models(tmp_path) == OPENCODE
    # Missing file / malformed provider block degrade to an empty map.
    assert read_opencode_gateway_models(tmp_path / "nope") == {}


def test_sync_render_is_idempotent_on_real_opencode_json():
    # The rewrite must be a no-op when the served set equals the current set, so
    # `make sync-gateway-models` produces a clean (empty) diff when in sync.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from sync_gateway_models import (
        OPENCODE_JSON,
        _find_models_span,
        _merge_models,
        _render_models_block,
    )

    text = OPENCODE_JSON.read_text()
    start, end = _find_models_span(text)
    current = json.loads(text[start:end])
    merged = _merge_models(current, list(current.keys()))
    rebuilt = text[:start] + _render_models_block(merged) + text[end:]
    assert rebuilt == text
