from __future__ import annotations

import json
import sys
from pathlib import Path

from harness_lab_app import gateway_models
from harness_lab_app.gateway_models import (
    DEFAULT_EXECUTOR_MODEL,
    DirectHostProbe,
    backend_unavailable_reason,
    build_model_catalog,
    classify_backend,
    classify_provider,
    gateway_model_id,
    normalize_model_name,
    read_opencode_gateway_models,
    strip_backend_prefix,
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


def test_backend_classification_routes_ids_to_their_serving_group():
    assert classify_backend("lmstudio-qwen3.6-35b")[1] == "lmstudio"
    assert classify_backend("ollama-qwen3.6-35b")[1] == "ollama"
    # Anything unprefixed is cloud, including backends nobody anticipated —
    # an unknown id must land in a group, never disappear from the menu.
    assert classify_backend("gpt-5.5")[1] == "cloud"
    assert classify_backend("sol-preview")[1] == "cloud"


def test_provider_classification_splits_the_cloud_bucket_by_vendor():
    """The vendor tier of the picker, which `classify_backend` cannot express.

    Every hosted model is one "cloud" backend, so without this the second tier
    would offer a single 30-entry heading — the wall of aliases the hierarchy
    exists to break up.
    """
    assert classify_provider("gpt-5.5") == ("openai", "OpenAI")
    assert classify_provider("claude-sonnet-5") == ("anthropic", "Claude")
    assert classify_provider("bedrock-claude-fable-5") == ("anthropic", "Claude")
    assert classify_provider("gemini-3.5-flash") == ("google", "Gemini")
    assert classify_provider("nim-nemotron-super-120b") == ("nvidia", "NVIDIA")
    assert classify_provider("lmstudio-qwen3.6-35b") == ("lmstudio", "LM Studio")
    assert classify_provider("ollama-qwen3.6-35b") == ("ollama", "Ollama")
    # Unmatched ids stay reachable rather than dropping out of the menu.
    assert classify_provider("sol-preview") == ("other", "Other")
    assert classify_provider("auto") == ("other", "Other")


def test_every_catalog_option_carries_its_picker_tiers():
    catalog = build_model_catalog(["gpt-5.5", "lmstudio-qwen3.6-35b-build"], OPENCODE)
    tiers = {
        m["id"]: (m["source"], m["provider"], m["provider_label"])
        for m in catalog["models"]
    }
    # Everything from this catalog is reached through the gateway; the option
    # `source` is a different axis from the catalog-level one ("gateway" here
    # means "the live list", not "how the model is reached").
    assert tiers["gateway/gpt-5.5"] == ("gateway", "openai", "OpenAI")
    assert tiers["gateway/lmstudio-qwen3.6-35b-build"] == (
        "gateway",
        "lmstudio",
        "LM Studio",
    )


def test_the_menu_puts_lmstudio_first_and_ollama_last():
    """The ordering contract behind the grouped run picker.

    Alphabetically ``lmstudio-*`` sorts *after* the cloud ids and interleaves
    with ``ollama-*``, so a flat sorted list buries the backend this lab
    actually runs on. Ollama is pinned last because those routes point at a
    host that is usually down.
    """
    catalog = build_model_catalog(
        ["ollama-qwen3.6-35b", "gpt-5.5", "lmstudio-qwen3.6-35b-build"],
        OPENCODE,
    )
    assert [m["backend"] for m in catalog["models"]] == [
        "lmstudio",
        "cloud",
        "ollama",
    ]
    assert catalog["models"][0]["group"] == "LM Studio (local)"


def test_the_menu_floats_launchable_models_above_unadopted_ones():
    """Within a backend, what you can actually run comes first.

    ``lmstudio-zeta`` is adopted (it has a curated label, so opencode can route
    it); ``lmstudio-alpha`` is served but declared nowhere and would fail at
    launch. Sorting by label alone would put "alpha" first and bury the only
    usable option — which is exactly how the default executor ended up at the
    bottom of its own group.
    """
    catalog = build_model_catalog(
        ["lmstudio-alpha", "lmstudio-zeta"], {"lmstudio-zeta": {"name": "Zeta build"}}
    )
    assert [m["id"] for m in catalog["models"]] == [
        "gateway/lmstudio-zeta",
        "gateway/lmstudio-alpha",
    ]


def test_the_menu_floats_curated_models_above_raw_ids():
    """A named model outranks one still carrying its bare gateway id.

    Both are routable here, so only curation separates them. Alphabetically
    "lmstudio-alpha" wins, which is precisely the ordering that buried the
    default executor at the bottom of the LM Studio group.
    """
    catalog = build_model_catalog(
        ["lmstudio-alpha", "lmstudio-zeta"],
        {"lmstudio-alpha": {}, "lmstudio-zeta": {"name": "Qwen3.6 35B build"}},
    )
    assert [m["label"] for m in catalog["models"]] == [
        "Qwen3.6 35B build",
        "lmstudio-alpha",
    ]


def test_the_menu_sorts_by_label_within_one_routability_tier():
    catalog = build_model_catalog(
        ["lmstudio-zeta", "lmstudio-alpha"],
        {"lmstudio-zeta": {"name": "Alpha zeta"}, "lmstudio-alpha": {"name": "Beta"}},
    )
    # Both are routable, so the human label decides — "Alpha zeta" before "Beta",
    # i.e. ordering follows what the user reads, not the underlying id.
    assert [m["label"] for m in catalog["models"]] == ["Alpha zeta", "Beta"]


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


# -- alias de-duplication ----------------------------------------------------

# Two aliases LiteLLM routes to the same backend model with the same sampling —
# one served model wearing two names — plus a genuinely distinct model.
ROUTING = {
    "lmstudio-qwen3.6-35b": {
        "target": "lm_studio/qwen/qwen3.6-35b-a3b",
        "api_base": "http://host:1234/v1",
        "params": {"temperature": 0.2},
    },
    "lmstudio-qwen3.6-35b-build": {
        "target": "lm_studio/qwen/qwen3.6-35b-a3b",
        "api_base": "http://host:1234/v1",
        "params": {"temperature": 0.2},
    },
    "gpt-5.5": {"target": "openai/gpt-5.5", "api_base": None, "params": {}},
}


def test_aliases_of_one_backend_model_collapse_into_one_entry():
    groups = gateway_models.group_aliases(
        ["lmstudio-qwen3.6-35b", "lmstudio-qwen3.6-35b-build", "gpt-5.5"], ROUTING
    )
    assert groups == [
        ("lmstudio-qwen3.6-35b", ["lmstudio-qwen3.6-35b-build"]),
        ("gpt-5.5", []),
    ]


def test_a_preferred_id_wins_the_name_of_its_group():
    """Merging must never silently repoint an arm the harness already routes."""
    groups = gateway_models.group_aliases(
        ["lmstudio-qwen3.6-35b", "lmstudio-qwen3.6-35b-build"],
        ROUTING,
        preferred=["lmstudio-qwen3.6-35b-build"],
    )
    assert groups == [("lmstudio-qwen3.6-35b-build", ["lmstudio-qwen3.6-35b"])]


def test_same_backend_but_different_sampling_stays_two_arms():
    """The real ``-build`` cards: same LM Studio model, different sampling.

    Merging them would pool two experimental conditions into one row, which is
    worse than a long list.
    """
    routing = {
        "plain": {
            "target": "lm_studio/qwen/qwen3.6-35b-a3b",
            "api_base": "http://host:1234/v1",
            "params": {"temperature": 0.7, "top_p": 0.8},
        },
        "build": {
            "target": "lm_studio/qwen/qwen3.6-35b-a3b",
            "api_base": "http://host:1234/v1",
            "params": {"temperature": 0.2, "max_tokens": 4096},
        },
    }
    assert gateway_models.group_aliases(["plain", "build"], routing) == [
        ("plain", []),
        ("build", []),
    ]


def test_models_the_gateway_cannot_describe_are_never_merged():
    """An unreachable /model/info degrades to the flat list, not to one blob."""
    groups = gateway_models.group_aliases(["a", "b", "c"], {})
    assert groups == [("a", []), ("b", []), ("c", [])]


def test_catalog_hides_merged_aliases_but_still_reports_them():
    """The id the harness already routes survives; the twin is listed under it."""
    catalog = build_model_catalog(
        ["lmstudio-qwen3.6-35b", "lmstudio-qwen3.6-35b-build", "gpt-5.5"],
        OPENCODE,
        routing=ROUTING,
    )
    ids = [m["id"] for m in catalog["models"]]
    assert ids == ["gateway/lmstudio-qwen3.6-35b-build", "gateway/gpt-5.5"]
    assert catalog["models"][0]["aliases"] == ["gateway/lmstudio-qwen3.6-35b"]


def test_a_merged_entry_is_routable_when_any_member_is():
    """The surviving id inherits routability, or merging would break the arm.

    Here the configured default is an id ``opencode.json`` does not declare, so
    the group is named after it while only the swallowed alias is routable.
    """
    catalog = build_model_catalog(
        ["lmstudio-qwen3.6-35b", "lmstudio-qwen3.6-35b-build"],
        OPENCODE,  # only the "-build" alias is declared in opencode.json
        default_executor="gateway/lmstudio-qwen3.6-35b",
        routing=ROUTING,
    )
    assert catalog["models"][0]["id"] == "gateway/lmstudio-qwen3.6-35b"
    assert catalog["models"][0]["routable"] is True


# -- backend liveness --------------------------------------------------------

HOST_DOWN = DirectHostProbe(
    root="http://host:1234",
    label="LM Studio",
    reachable=False,
    error="timed out",
)
HOST_UP = DirectHostProbe(
    root="http://host:1234",
    label="LM Studio",
    reachable=True,
    model_ids=frozenset({"qwen/qwen3.6-35b-a3b"}),
)


def test_strip_backend_prefix_keeps_the_lm_studio_publisher():
    """``lm_studio/openai/...`` carries the provider AND the publisher."""
    assert strip_backend_prefix("lm_studio/openai/gpt-oss-20b") == "openai/gpt-oss-20b"
    assert strip_backend_prefix("ollama_chat/qwen3.6:27b") == "qwen3.6:27b"
    assert strip_backend_prefix("openai/gpt-4o") == "openai/gpt-4o"


def test_a_card_is_unavailable_when_its_host_is_not_answering():
    reason = backend_unavailable_reason(ROUTING["lmstudio-qwen3.6-35b"], [HOST_DOWN])
    assert reason is not None
    assert "http://host:1234" in reason
    assert "timed out" in reason


def test_a_card_is_unavailable_when_the_host_does_not_have_that_model():
    """The alias resolves, the machine is up, and the id is simply not there."""
    entry = {
        "target": "lm_studio/google/gemma-4-31b-qat",
        "api_base": "http://host:1234/v1",
        "params": {},
    }
    reason = backend_unavailable_reason(entry, [HOST_UP])
    assert reason is not None
    assert "google/gemma-4-31b-qat" in reason


def test_a_card_the_host_serves_is_available():
    assert (
        backend_unavailable_reason(ROUTING["lmstudio-qwen3.6-35b"], [HOST_UP]) is None
    )


def test_a_self_hosted_card_with_no_api_base_is_unavailable_without_a_probe():
    """The real ``lmstudio-qwen3.5-9b-mac16``: its env var was never set.

    LiteLLM falls back to its own default host, so the card cannot work — and
    that is decidable from the routing alone, before any machine is asked.
    """
    entry = {"target": "lm_studio/qwen/qwen3.5-9b", "api_base": None, "params": {}}
    reason = backend_unavailable_reason(entry, [])
    assert reason is not None
    assert "api_base" in reason


def test_a_cloud_card_is_never_judged_by_a_lan_probe():
    """``openai/gpt-5.5`` has no api_base because it needs none."""
    assert backend_unavailable_reason(ROUTING["gpt-5.5"], [HOST_DOWN]) is None


def test_an_unprobed_host_leaves_the_verdict_open():
    """Fail-open: a host the lab does not read must not read as broken."""
    elsewhere = DirectHostProbe(
        root="http://other:1234", label="LM Studio", reachable=False
    )
    assert (
        backend_unavailable_reason(ROUTING["lmstudio-qwen3.6-35b"], [elsewhere]) is None
    )


def test_the_catalog_reports_why_a_card_cannot_answer():
    catalog = build_model_catalog(
        ["lmstudio-qwen3.6-35b-build", "gpt-5.5"],
        OPENCODE,
        routing=ROUTING,
        probes=[HOST_DOWN],
    )
    by_id = {m["id"]: m for m in catalog["models"]}
    assert by_id["gateway/lmstudio-qwen3.6-35b-build"]["reachable"] is False
    assert (
        "not answering"
        in by_id["gateway/lmstudio-qwen3.6-35b-build"]["unreachable_reason"]
    )
    # Undecidable, not healthy: the field must stay open for a cloud card.
    assert by_id["gateway/gpt-5.5"]["reachable"] is None
    assert by_id["gateway/gpt-5.5"]["unreachable_reason"] is None


def test_the_catalog_leaves_every_verdict_open_without_probes():
    """No probes is the pre-existing behaviour, unchanged."""
    catalog = build_model_catalog(["lmstudio-qwen3.6-35b"], OPENCODE, routing=ROUTING)
    assert catalog["models"][0]["reachable"] is None


def test_the_menu_sinks_a_dead_backend_below_a_working_one():
    """Both are routable and curated; only one has a machine behind it."""
    catalog = build_model_catalog(
        ["lmstudio-qwen3.6-35b-build", "lmstudio-gemma4-31b"],
        {
            "lmstudio-qwen3.6-35b-build": {"name": "AAA down"},
            "lmstudio-gemma4-31b": {"name": "ZZZ up"},
        },
        routing={
            **ROUTING,
            "lmstudio-gemma4-31b": {
                "target": "lm_studio/qwen/qwen3.6-35b-a3b",
                "api_base": "http://other:1234/v1",
                "params": {"temperature": 0.9},
            },
        },
        probes=[HOST_DOWN],
    )
    # "AAA down" sorts first by label and still lands last: liveness outranks it.
    assert [m["label"] for m in catalog["models"]] == ["ZZZ up", "AAA down"]


# -- context preflight -------------------------------------------------------

# Verbatim llama.cpp refusal from run tetris-v1-K-020, the failure this probe
# exists to catch before a run burns 2.5 hours on it.
N_KEEP_ERROR = (
    '{"error":{"message":"The number of tokens to keep from the initial prompt '
    'is greater than the context length (n_keep: 13777 >= n_ctx: 8192)"}}'
)


def _stub_probe(monkeypatch, *, accepted, payload=None, raw="", code=None):
    monkeypatch.setattr(
        gateway_models,
        "_send_context_probe",
        lambda *_a, **_kw: (accepted, payload, raw, code),
    )


def test_a_small_loaded_window_is_reported_with_its_real_size(monkeypatch):
    _stub_probe(monkeypatch, accepted=False, raw=N_KEEP_ERROR, code=500)
    result = gateway_models.probe_model_context(
        "m", base_url="https://gw/v1", token="t"
    )
    assert result.status == "insufficient"
    assert result.context_tokens == 8192
    assert not result.ok


def test_an_accepted_oversized_prompt_passes(monkeypatch):
    _stub_probe(monkeypatch, accepted=True, payload={"usage": {"prompt_tokens": 34426}})
    result = gateway_models.probe_model_context(
        "m", base_url="https://gw/v1", token="t"
    )
    assert result.ok
    assert result.context_tokens == 34426


def test_an_output_budget_complaint_counts_as_input_accepted(monkeypatch):
    """Reasoning models refuse a small max_tokens *after* reading the prompt."""
    _stub_probe(
        monkeypatch,
        accepted=False,
        raw="Could not finish the message because max_tokens or model output "
        "limit was reached",
        code=400,
    )
    result = gateway_models.probe_model_context(
        "m", base_url="https://gw/v1", token="t"
    )
    assert result.status == "ok"


def test_an_unrelated_failure_never_blocks_a_run(monkeypatch):
    """Fail open: only a *provable* small window is allowed to stop a run."""
    _stub_probe(monkeypatch, accepted=False, raw="rate limit exceeded", code=429)
    result = gateway_models.probe_model_context(
        "m", base_url="https://gw/v1", token="t"
    )
    assert result.status == "unknown"


def test_a_dense_tokenizer_triggers_one_corrective_retry(monkeypatch):
    """The filler ratio differs per model, so a shortfall is measured and fixed."""
    seen: list[int] = []

    def _fake(_model, _base, _token, n_chars, _timeout):
        seen.append(n_chars)
        # First call undershoots the 32768 requirement; the retry clears it.
        tokens = 23958 if len(seen) == 1 else 34426
        return True, {"usage": {"prompt_tokens": tokens}}, "", None

    monkeypatch.setattr(gateway_models, "_send_context_probe", _fake)
    result = gateway_models.probe_model_context(
        "m", base_url="https://gw/v1", token="t"
    )
    assert result.ok
    assert len(seen) == 2 and seen[1] > seen[0]


def test_a_shortfall_that_survives_the_retry_is_inconclusive(monkeypatch):
    _stub_probe(monkeypatch, accepted=True, payload={"usage": {"prompt_tokens": 100}})
    result = gateway_models.probe_model_context(
        "m", base_url="https://gw/v1", token="t"
    )
    assert result.status == "unknown"
    assert result.prompt_tokens == 100


def test_only_a_conclusive_verdict_is_cached(monkeypatch):
    calls: list[int] = []

    def _fake(*_a, **_kw):
        calls.append(1)
        return (True, {"usage": {"prompt_tokens": 100}}, "", None)

    monkeypatch.setattr(gateway_models, "_send_context_probe", _fake)
    gateway_models.clear_context_cache()
    gateway_models.probe_model_context_cached("m", base_url="https://gw/v1", token="t")
    gateway_models.probe_model_context_cached("m", base_url="https://gw/v1", token="t")
    # "unknown" must be retried, not replayed for ten minutes.
    assert len(calls) == 4  # two probes, each with its corrective retry
