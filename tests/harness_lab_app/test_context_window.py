"""Served-window detection: the limit opencode is told follows the host.

Root cause (measured 2026-09-22 against the lab's LM Studio box): no
``lmstudio-*`` card declared ``limit.context``, opencode therefore never
compacted, the session outgrew the 41k window, LM Studio truncated every
prompt from the middle, and each turn paid a full ~60s prefill because the
cached prefix never matched again. The fix is to declare the window — but
the window is whatever the operator loaded, so it is read, not written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_lab_app import (
    context_window,
    gateway_models,
    lmstudio_direct,
    ollama_direct,
)
from harness_lab_app.context_window import (
    DEFAULT_OUTPUT_TOKENS,
    ENV_AUTODETECT,
    ENV_DIRECT_OUTPUT_TOKENS,
    ENV_DISABLE_PROBE,
    MIN_HEADROOM_TOKENS,
    ContextWindow,
    autodetect_enabled,
    describe,
    detect_window,
    detect_windows,
    headroom_for,
    limit_for,
    limits_by_model,
)
from harness_lab_app.lmstudio_direct import LMStudioModel
from harness_lab_app.ollama_direct import OllamaModel
from harness_lab_app.opencode_overlay import build_config, build_overlay

LOADED_35B = LMStudioModel(
    id="qwen/qwen3.6-35b-a3b",
    type="llm",
    state="loaded",
    max_context=262144,
    loaded_context=41216,
)
UNLOADED_27B = LMStudioModel(
    id="qwen/qwen3.6-27b",
    type="llm",
    state="not-loaded",
    max_context=262144,
    loaded_context=None,
)
ROUTING = {
    "lmstudio-qwen3.6-35b-build": {
        "target": "lm_studio/qwen/qwen3.6-35b-a3b",
        "api_base": "http://gpu:1234/v1",
        "params": {"max_tokens": 4096, "temperature": 0.2},
    },
    "lmstudio-qwen3.6-27b-build": {
        "target": "lm_studio/qwen/qwen3.6-27b",
        "api_base": "http://gpu:1234/v1",
        "params": {"max_tokens": 4096},
    },
    "gpt-5.5": {"target": "openai/gpt-5.5", "api_base": None, "params": {}},
}


@pytest.fixture
def lmstudio(monkeypatch):
    """A host serving the 35B at 41k and holding the 27B unloaded."""
    calls: list[str] = []

    def fake_list(base_url=None, timeout=8.0):
        calls.append(base_url)
        return [LOADED_35B, UNLOADED_27B]

    monkeypatch.setattr(lmstudio_direct, "list_lmstudio_models", fake_list)
    monkeypatch.delenv(ENV_DISABLE_PROBE, raising=False)
    return calls


# -- the arithmetic ----------------------------------------------------------


def test_headroom_is_ten_percent_with_a_floor():
    assert headroom_for(41216) == 4121
    assert headroom_for(120000) == 12000
    assert headroom_for(16384) == MIN_HEADROOM_TOKENS


def test_the_limit_leaves_room_for_the_next_tool_output():
    assert limit_for(41216, 4096) == {"context": 37095, "output": 4096}
    assert limit_for(120000, 4096) == {"context": 108000, "output": 4096}


def test_a_window_too_small_to_declare_is_left_to_the_preflight():
    # 8k JIT default: 8192 - 4096 headroom = 4096, not above the output cap.
    assert limit_for(8192, 4096) is None


# -- the switch --------------------------------------------------------------


def test_autodetect_is_on_by_default_and_off_with_the_probe_kill_switch():
    assert autodetect_enabled({}) is True
    assert autodetect_enabled({ENV_AUTODETECT: "0"}) is False
    assert autodetect_enabled({ENV_AUTODETECT: "false"}) is False
    assert autodetect_enabled({ENV_DISABLE_PROBE: "1"}) is False


# -- reading the window ------------------------------------------------------


def test_a_direct_lmstudio_model_reads_its_own_host(lmstudio):
    window = detect_window(
        "lmstudio/qwen/qwen3.6-35b-a3b", lmstudio_base="http://gpu:1234/v1"
    )
    assert window == ContextWindow(
        model="lmstudio/qwen/qwen3.6-35b-a3b",
        backend="lmstudio",
        host="http://gpu:1234",
        bare_id="qwen/qwen3.6-35b-a3b",
        served=41216,
        context=37095,
        output=DEFAULT_OUTPUT_TOKENS,
    )
    assert lmstudio == ["http://gpu:1234"]


def test_a_gateway_alias_is_followed_to_the_host_its_card_names(lmstudio):
    """The gateway card, not the lab's own variable, says where the model is."""
    window = detect_window(
        "gateway/lmstudio-qwen3.6-35b-build",
        routing=ROUTING,
        lmstudio_base="http://elsewhere:1234/v1",
    )
    assert window is not None
    assert window.host == "http://gpu:1234"
    assert window.served == 41216
    # The card's own output cap becomes limit.output, not the direct default.
    assert window.output == 4096
    assert lmstudio[0] == "http://gpu:1234"


def test_a_gateway_card_spelling_localhost_falls_back_to_the_lab_variable(
    monkeypatch,
):
    """From the gateway's container the host is ``localhost``; from here it is not."""
    hosts: list[str] = []

    def fake_list(base_url=None, timeout=8.0):
        hosts.append(base_url)
        if base_url == "http://localhost:1234":
            raise lmstudio_direct.LMStudioUnreachable("connection refused")
        return [LOADED_35B]

    monkeypatch.setattr(lmstudio_direct, "list_lmstudio_models", fake_list)
    routing = {
        "alias": {
            "target": "lm_studio/qwen/qwen3.6-35b-a3b",
            "api_base": "http://localhost:1234/v1",
            "params": {},
        }
    }
    window = detect_window(
        "gateway/alias", routing=routing, lmstudio_base="http://gpu:1234/v1"
    )
    assert window is not None
    assert window.host == "http://gpu:1234"
    assert hosts == ["http://localhost:1234", "http://gpu:1234"]


def test_a_model_that_is_not_loaded_reports_nothing(lmstudio):
    """JIT would load it with its default window; that number is not known."""
    assert (
        detect_window(
            "gateway/lmstudio-qwen3.6-27b-build",
            routing=ROUTING,
            lmstudio_base="http://gpu:1234/v1",
        )
        is None
    )


def test_a_cloud_model_and_an_unknown_alias_report_nothing(lmstudio):
    assert detect_window("gateway/gpt-5.5", routing=ROUTING) is None
    assert detect_window("gateway/not-in-routing", routing=ROUTING) is None
    assert detect_window("openai/gpt-4o", routing=ROUTING) is None
    assert lmstudio == []


def test_a_dead_host_is_not_an_error_here(monkeypatch):
    def boom(base_url=None, timeout=8.0):
        raise lmstudio_direct.LMStudioUnreachable("server is off")

    monkeypatch.setattr(lmstudio_direct, "list_lmstudio_models", boom)
    assert detect_window("lmstudio/x", lmstudio_base="http://gpu:1234/v1") is None


def test_an_ollama_model_reads_the_running_window(monkeypatch):
    monkeypatch.setattr(
        ollama_direct,
        "list_ollama_models",
        lambda *_a, **_k: [
            OllamaModel(id="qwen3.6:35b", running=True, context_length=65536)
        ],
    )
    window = detect_window("ollama/qwen3.6:35b", ollama_base="http://gpu:11434/v1")
    assert window is not None
    assert (window.backend, window.served, window.context) == (
        "ollama",
        65536,
        65536 - 6553,
    )
    via_gateway = detect_window(
        "gateway/ollama-build",
        routing={
            "ollama-build": {
                "target": "ollama_chat/qwen3.6:35b",
                "api_base": "http://gpu:11434",
                "params": {"max_tokens": 2048},
            }
        },
    )
    assert via_gateway is not None
    assert via_gateway.output == 2048


def test_the_direct_output_cap_is_configurable(lmstudio, monkeypatch):
    monkeypatch.setenv(ENV_DIRECT_OUTPUT_TOKENS, "2048")
    window = detect_window(
        "lmstudio/qwen/qwen3.6-35b-a3b", lmstudio_base="http://gpu:1234/v1"
    )
    assert window is not None
    assert window.output == 2048


def test_detect_windows_shares_one_routing_fetch(lmstudio, monkeypatch):
    fetches: list[tuple] = []

    def fake_routing(base_url=None, token=None):
        fetches.append((base_url, token))
        return ROUTING

    monkeypatch.setattr(gateway_models, "fetch_gateway_routing", fake_routing)
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://gpu:1234/v1")
    windows = detect_windows(
        [
            "gateway/lmstudio-qwen3.6-35b-build",
            "gateway/gpt-5.5",
            "gateway/lmstudio-qwen3.6-35b-build",
            "",
        ],
        gateway_base="http://gw:4000",
        gateway_token="sk",
    )
    assert fetches == [("http://gw:4000", "sk")]
    assert [w.model for w in windows] == ["gateway/lmstudio-qwen3.6-35b-build"]
    assert limits_by_model(windows) == {
        "gateway/lmstudio-qwen3.6-35b-build": {"context": 37095, "output": 4096}
    }


def test_detect_windows_skips_the_gateway_when_no_alias_needs_it(lmstudio, monkeypatch):
    monkeypatch.setattr(
        gateway_models,
        "fetch_gateway_routing",
        lambda *_a, **_k: pytest.fail("no gateway round-trip was needed"),
    )
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://gpu:1234/v1")
    assert len(detect_windows(["lmstudio/qwen/qwen3.6-35b-a3b"])) == 1


def test_describe_names_the_window_and_the_limit():
    window = ContextWindow(
        "gateway/a", "lmstudio", "http://gpu:1234", "a", 41216, 37120, 4096
    )
    line = describe(window)
    assert "41,216" in line and "37,120" in line and "http://gpu:1234" in line


# -- through the overlay -----------------------------------------------------

TRACKED = {
    "npm": "@ai-sdk/openai-compatible",
    "name": "AI Gateway",
    "options": {"baseURL": "{env:GATEWAY_BASE_URL}"},
    "models": {"lmstudio-35b": {"name": "35B"}, "gpt-5.5": {"name": "GPT-5.5"}},
}


def test_build_config_writes_the_limit_onto_the_declared_model_only():
    config = build_config(
        TRACKED,
        {},
        ["lmstudio/qwen/x"],
        limits={
            "gateway/lmstudio-35b": {"context": 37120, "output": 4096},
            "lmstudio/qwen/x": {"context": 100000, "output": 4096},
            "gateway/never-declared": {"context": 1, "output": 1},
            "ollama/absent": {"context": 1, "output": 1},
        },
    )
    gateway = config["provider"]["gateway"]["models"]
    assert gateway["lmstudio-35b"] == {
        "name": "35B",
        "limit": {"context": 37120, "output": 4096},
    }
    assert "limit" not in gateway["gpt-5.5"]
    assert "never-declared" not in gateway
    assert config["provider"]["lmstudio"]["models"]["qwen/x"]["limit"] == {
        "context": 100000,
        "output": 4096,
    }
    assert "ollama" not in config["provider"]


def test_a_detected_window_alone_produces_an_overlay_and_enters_the_hash(
    tmp_path: Path,
):
    (tmp_path / "opencode.json").write_text(
        json.dumps({"provider": {"gateway": TRACKED}})
    )
    assert build_overlay(tmp_path) == ({}, None)

    env, digest = build_overlay(
        tmp_path, limits={"gateway/lmstudio-35b": {"context": 37120, "output": 4096}}
    )
    assert digest is not None
    inline = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert inline["provider"]["gateway"]["models"]["lmstudio-35b"]["limit"] == {
        "context": 37120,
        "output": 4096,
    }
    # A different window is a different experimental condition.
    _, other = build_overlay(
        tmp_path, limits={"gateway/lmstudio-35b": {"context": 108000, "output": 4096}}
    )
    assert other != digest
    # An empty limit is no limit.
    assert build_overlay(tmp_path, limits={"gateway/lmstudio-35b": {}}) == ({}, None)


def test_the_module_stays_stdlib_only():
    """``bench_run.py`` imports it inside the runner container (no web deps)."""
    source = Path(context_window.__file__).read_text()
    for forbidden in ("fastapi", "pydantic", "sqlalchemy", "requests", "httpx"):
        assert forbidden not in source
