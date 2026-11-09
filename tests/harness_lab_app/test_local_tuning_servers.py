"""Adapter tests for :mod:`harness_lab_app.local_tuning.servers`.

No network: :func:`harness_lab_app.local_tuning.servers._http` is monkeypatched
with a fake router so every test drives recorded LM Studio 0.4.21 responses and
hand-written Ollama fixtures (see tests/harness_lab_app/fixtures/local_tuning/).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_lab_app.direct_hosts import DirectHostUnreachable
from harness_lab_app.local_tuning import servers
from harness_lab_app.local_tuning.contracts import LoadConfig, ServedConfig, ServerError

FIXTURES = Path(__file__).parent / "fixtures" / "local_tuning"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeRouter:
    """Routes ``(url, payload)`` to a fixture, recording every call made."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, url: str, timeout: float, payload: dict | None = None) -> dict:
        self.calls.append((url, payload))
        for suffix, response in self.routes.items():
            if url.endswith(suffix):
                if isinstance(response, Exception):
                    raise response
                return response() if callable(response) else response
        raise AssertionError(f"unrouted call: {url}")


@pytest.fixture
def lmstudio() -> servers.LMStudioServer:
    return servers.LMStudioServer("http://localhost:1234", timeout=1.0)


@pytest.fixture
def ollama() -> servers.OllamaServer:
    return servers.OllamaServer("http://localhost:11434", timeout=1.0)


# --- LM Studio: list_models ---------------------------------------------


def test_lmstudio_list_models_from_v1_fixture(monkeypatch, lmstudio):
    router = FakeRouter({"/api/v1/models": _load("lmstudio_v1_models.json")})
    monkeypatch.setattr(servers, "_http", router)

    models = lmstudio.list_models()

    by_id = {m.id: m for m in models}
    assert "text-embedding-nomic-embed-text-v1.5" not in by_id

    bonsai = by_id["prism-ml/bonsai-27b"]
    assert bonsai.loaded is True
    assert bonsai.instance_ids == ["prism-ml/bonsai-27b"]
    assert bonsai.served is not None
    assert bonsai.served.context_length == 50432
    assert bonsai.served.flash_attention is True
    assert bonsai.served.offload_kv_cache_to_gpu is True

    moe = by_id["qwen/qwen3.6-35b-a3b"]
    assert moe.is_moe is True
    assert moe.loaded is False
    assert moe.served is None


def test_lmstudio_list_models_falls_back_to_v0_on_404(monkeypatch, lmstudio):
    import io
    import urllib.error

    def fake_get_json(url, timeout, payload=None):
        if url.endswith("/api/v1/models"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        if url.endswith("/api/v0/models"):
            return _load("lmstudio_v0_models.json")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(servers, "get_json", fake_get_json)

    models = lmstudio.list_models()

    by_id = {m.id: m for m in models}
    bonsai = by_id["prism-ml/bonsai-27b"]
    assert bonsai.loaded is True
    assert bonsai.served is not None
    assert bonsai.served.context_length == 50432
    assert bonsai.supports_tools is True
    assert "text-embedding-nomic-embed-text-v1.5" not in by_id


# --- LM Studio: load / unload --------------------------------------------


def test_lmstudio_load_omits_none_fields_and_sends_echo(monkeypatch, lmstudio):
    captured: dict = {}

    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/v1/models/load"):
            captured["payload"] = payload
            return {
                "load_config": {
                    "context_length": 50432,
                    "flash_attention": True,
                    "offload_kv_cache_to_gpu": True,
                    "eval_batch_size": 2048,
                    "parallel": 1,
                }
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(servers, "_http", fake_http)

    config = LoadConfig(context_length=65536)
    served = lmstudio.load("prism-ml/bonsai-27b", config)

    assert captured["payload"]["echo_load_config"] is True
    assert captured["payload"]["context_length"] == 65536
    assert "eval_batch_size" not in captured["payload"]
    assert "flash_attention" not in captured["payload"]
    assert "num_experts" not in captured["payload"]
    assert "gpu_offload" not in captured["payload"]

    assert served.context_length == 50432
    assert served.flash_attention is True
    assert served.offload_kv_cache_to_gpu is True


def test_lmstudio_load_reads_back_list_models_when_load_config_absent(
    monkeypatch, lmstudio
):
    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/v1/models/load"):
            return {}
        if url.endswith("/api/v1/models"):
            return _load("lmstudio_v1_models.json")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(servers, "_http", fake_http)

    served = lmstudio.load("prism-ml/bonsai-27b", LoadConfig(context_length=50432))

    assert served.context_length == 50432
    assert served.flash_attention is True


def test_lmstudio_load_returns_instance_id_from_response(monkeypatch, lmstudio):
    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/v1/models/load"):
            return {
                "instance_id": "prism-ml/bonsai-27b:3",
                "load_config": {"context_length": 50432},
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(servers, "_http", fake_http)

    served = lmstudio.load("prism-ml/bonsai-27b", LoadConfig(context_length=65536))

    assert served.instance_id == "prism-ml/bonsai-27b:3"


def test_lmstudio_load_falls_back_to_model_key_without_instance_id(
    monkeypatch, lmstudio
):
    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/v1/models/load"):
            return {"load_config": {"context_length": 50432}}
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(servers, "_http", fake_http)

    served = lmstudio.load("prism-ml/bonsai-27b", LoadConfig(context_length=65536))

    assert served.instance_id == "prism-ml/bonsai-27b"


def test_lmstudio_unload_with_instance_id_targets_only_that_instance(
    monkeypatch, lmstudio
):
    calls: list[dict] = []

    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/v1/models/unload"):
            calls.append(payload)
            return {}
        raise AssertionError(f"unexpected url: {url} (should not list models)")

    monkeypatch.setattr(servers, "_http", fake_http)

    lmstudio.unload("prism-ml/bonsai-27b", instance_id="prism-ml/bonsai-27b:2")

    assert calls == [{"instance_id": "prism-ml/bonsai-27b:2"}]


def test_lmstudio_unload_posts_each_instance_id(monkeypatch, lmstudio):
    calls: list[dict] = []

    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/v1/models"):
            payload_out = _load("lmstudio_v1_models.json")
            # Give bonsai two loaded instances to exercise the loop.
            payload_out["models"][1]["loaded_instances"].append(
                {"id": "prism-ml/bonsai-27b:2", "config": {}}
            )
            return payload_out
        if url.endswith("/api/v1/models/unload"):
            calls.append(payload)
            return {}
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(servers, "_http", fake_http)

    lmstudio.unload("prism-ml/bonsai-27b")

    assert {c["instance_id"] for c in calls} == {
        "prism-ml/bonsai-27b",
        "prism-ml/bonsai-27b:2",
    }


def test_lmstudio_unload_is_a_no_op_when_not_loaded(monkeypatch, lmstudio):
    router = FakeRouter({"/api/v1/models": _load("lmstudio_v1_models.json")})
    monkeypatch.setattr(servers, "_http", router)

    lmstudio.unload("qwen/qwen3.8-27b")  # never loaded in the fixture

    assert all("unload" not in call[0] for call in router.calls)


# --- LM Studio: complete ---------------------------------------------------


def test_lmstudio_complete_reasoning_model_has_empty_text_but_stats(
    monkeypatch, lmstudio
):
    router = FakeRouter({"/api/v0/chat/completions": _load("lmstudio_v0_chat.json")})
    monkeypatch.setattr(servers, "_http", router)

    completion = lmstudio.complete("prism-ml/bonsai-27b", "Say hello in five words.")

    assert completion.text == ""
    assert completion.prompt_tokens == 16
    assert completion.completion_tokens == 32
    assert completion.tokens_per_second == pytest.approx(79.38011809713053)
    assert completion.time_to_first_token_s == pytest.approx(0.24906299999999998)
    assert completion.prompt_tokens_per_second is not None
    assert completion.error == ""


def test_lmstudio_complete_parses_tool_call(monkeypatch, lmstudio):
    router = FakeRouter(
        {"/api/v0/chat/completions": _load("lmstudio_v0_chat_tool.json")}
    )
    monkeypatch.setattr(servers, "_http", router)

    completion = lmstudio.complete(
        "prism-ml/bonsai-27b", "Read notes.txt", tools=[{"type": "function"}]
    )

    assert completion.tool_calls == [
        {"name": "read_file", "arguments": {"path": "notes.txt"}}
    ]


def test_lmstudio_complete_flags_invalid_json_arguments(monkeypatch, lmstudio):
    fixture = _load("lmstudio_v0_chat_tool.json")
    fixture["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
        "{not json"
    )
    router = FakeRouter({"/api/v0/chat/completions": fixture})
    monkeypatch.setattr(servers, "_http", router)

    completion = lmstudio.complete("prism-ml/bonsai-27b", "Read notes.txt")

    call = completion.tool_calls[0]
    assert call["name"] == "read_file"
    assert call["arguments"]["_invalid_json"] is True
    assert call["arguments"]["_raw"] == "{not json"


def test_lmstudio_complete_returns_completion_error_on_server_error(
    monkeypatch, lmstudio
):
    def fake_http(url, timeout, payload=None):
        raise ServerError("context length exceeds the loaded window")

    monkeypatch.setattr(servers, "_http", fake_http)

    completion = lmstudio.complete("prism-ml/bonsai-27b", "x" * 100000)

    assert completion.error == "context length exceeds the loaded window"
    assert completion.text == ""


# --- Unreachable / HTTP error handling -------------------------------------


def test_http_translates_url_error_to_direct_host_unreachable(monkeypatch, lmstudio):
    import urllib.error

    def fake_get_json(url, timeout, payload=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(servers, "get_json", fake_get_json)

    with pytest.raises(DirectHostUnreachable) as excinfo:
        lmstudio.list_models()

    assert "http://localhost:1234/api/v1/models" in str(excinfo.value)


def test_http_translates_http_error_to_server_error_with_server_text(
    monkeypatch, lmstudio
):
    import io
    import urllib.error

    def fake_get_json(url, timeout, payload=None):
        body = json.dumps({"error": "context_length must be at least 512"}).encode()
        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(servers, "get_json", fake_get_json)

    with pytest.raises(ServerError) as excinfo:
        lmstudio.load("prism-ml/bonsai-27b", LoadConfig(context_length=600))

    assert str(excinfo.value) == "context_length must be at least 512"


def test_http_error_on_complete_returns_completion_error_not_raise(
    monkeypatch, lmstudio
):
    import io
    import urllib.error

    def fake_get_json(url, timeout, payload=None):
        body = json.dumps({"error": "model not loaded"}).encode()
        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(servers, "get_json", fake_get_json)

    completion = lmstudio.complete("prism-ml/bonsai-27b", "hello")

    assert completion.error == "model not loaded"


# --- Ollama -----------------------------------------------------------------


def test_ollama_list_models_exact_shape_from_show_and_served_from_ps(
    monkeypatch, ollama
):
    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/tags"):
            return _load("ollama_tags.json")
        if url.endswith("/api/ps"):
            return _load("ollama_ps.json")
        if url.endswith("/api/show"):
            assert payload == {"model": "qwen3:8b"} or payload == {
                "model": "llama3.1:70b"
            }
            if payload["model"] == "qwen3:8b":
                return _load("ollama_show.json")
            raise ServerError("no response")  # llama entry keeps no shape
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(servers, "_http", fake_http)

    models = ollama.list_models()
    by_id = {m.id: m for m in models}

    qwen = by_id["qwen3:8b"]
    assert qwen.loaded is True
    assert qwen.served is not None
    assert qwen.served.context_length == 32768
    assert qwen.served.vram_bytes == 6830000000
    assert qwen.shape is not None
    assert qwen.shape.layers == 36
    assert qwen.shape.kv_heads == 8
    assert qwen.shape.head_dim == 128

    llama = by_id["llama3.1:70b"]
    assert llama.loaded is False
    assert llama.shape is None


def test_ollama_load_sends_num_ctx(monkeypatch, ollama):
    captured: dict = {}

    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/generate"):
            captured["payload"] = payload
            return {}
        if url.endswith("/api/ps"):
            return _load("ollama_ps.json")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(servers, "_http", fake_http)

    served = ollama.load("qwen3:8b", LoadConfig(context_length=32768))

    assert captured["payload"]["options"]["num_ctx"] == 32768
    assert captured["payload"]["keep_alive"] == "30m"
    assert served.context_length == 32768
    assert served.instance_id == "qwen3:8b"


def test_ollama_unload_sends_keep_alive_zero(monkeypatch, ollama):
    captured: dict = {}

    def fake_http(url, timeout, payload=None):
        captured["url"] = url
        captured["payload"] = payload
        return {}

    monkeypatch.setattr(servers, "_http", fake_http)

    ollama.unload("qwen3:8b")

    assert captured["url"].endswith("/api/generate")
    assert captured["payload"] == {"model": "qwen3:8b", "keep_alive": 0}


def test_ollama_complete_timing_math(monkeypatch, ollama):
    router = FakeRouter({"/api/chat": _load("ollama_chat.json")})
    monkeypatch.setattr(servers, "_http", router)

    completion = ollama.complete(
        "qwen3:8b", "Say hi", config=LoadConfig(context_length=32768)
    )

    assert completion.prompt_tokens == 16
    assert completion.completion_tokens == 48
    assert completion.prompt_tokens_per_second == pytest.approx(16 / (120000000 / 1e9))
    assert completion.tokens_per_second == pytest.approx(48 / (980000000 / 1e9))
    assert completion.time_to_first_token_s == pytest.approx(
        (120000000 + 15000000) / 1e9
    )


def test_ollama_complete_parses_tool_call(monkeypatch, ollama):
    router = FakeRouter({"/api/chat": _load("ollama_chat_tool.json")})
    monkeypatch.setattr(servers, "_http", router)

    completion = ollama.complete("qwen3:8b", "Read notes.txt")

    assert completion.tool_calls == [
        {"name": "read_file", "arguments": {"path": "notes.txt"}}
    ]


# --- validate_server_url (SSRF hardening) ----------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:1234",
        "http://localhost:1234/v1",
        "http://localhost:1234/api",
        "http://localhost:1234/v1/",
        "https://192.168.0.10:1234",
    ],
)
def test_validate_server_url_accepts_plain_addresses(url):
    assert servers.validate_server_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://localhost:1234",
        "http://localhost:1234/foo",
        "http://localhost:1234?x=1",
        "http://localhost:1234#frag",
        "http://user:pass@localhost:1234",
        "http://localhost:abc",
        "file:///etc/passwd",
    ],
)
def test_validate_server_url_rejects_everything_else(url):
    with pytest.raises(servers.InvalidServerUrl):
        servers.validate_server_url(url)


def test_make_server_rejects_invalid_url():
    with pytest.raises(servers.InvalidServerUrl):
        servers.make_server("lmstudio", "http://localhost:1234/evil?x=1")


# --- _http: ValueError translation ------------------------------------------


def test_http_translates_value_error_to_server_error(monkeypatch, lmstudio):
    def fake_get_json(url, timeout, payload=None):
        raise ValueError("unknown url type: 'bogus'")

    monkeypatch.setattr(servers, "get_json", fake_get_json)

    with pytest.raises(ServerError) as excinfo:
        lmstudio.list_models()

    assert "invalid response" in str(excinfo.value)


def test_http_translates_json_decode_error_to_server_error(monkeypatch, lmstudio):
    import json as json_mod

    def fake_get_json(url, timeout, payload=None):
        raise json_mod.JSONDecodeError("bad json", "not json", 0)

    monkeypatch.setattr(servers, "get_json", fake_get_json)

    with pytest.raises(ServerError):
        lmstudio.list_models()


def test_http_truncates_long_error_messages(monkeypatch, lmstudio):
    def fake_get_json(url, timeout, payload=None):
        raise ValueError("x" * 1000)

    monkeypatch.setattr(servers, "get_json", fake_get_json)

    with pytest.raises(ServerError) as excinfo:
        lmstudio.list_models()

    assert len(str(excinfo.value)) <= 320


# --- unsupported_settings / diff_config ------------------------------------


def test_unsupported_settings_ollama_flash_attention_and_kv_cache():
    config = LoadConfig(
        context_length=8192, flash_attention=True, kv_cache_bytes=1.0625
    )
    hints = servers.unsupported_settings("ollama", config)
    assert any("OLLAMA_FLASH_ATTENTION" in hint for hint in hints)
    assert any("OLLAMA_KV_CACHE_TYPE" in hint for hint in hints)


def test_unsupported_settings_lmstudio_gpu_offload():
    config = LoadConfig(context_length=8192, gpu_offload=0.5)
    hints = servers.unsupported_settings("lmstudio", config)
    assert any("GPU offload" in hint for hint in hints)


def test_unsupported_settings_none_when_fully_supported():
    config = LoadConfig(context_length=8192)
    assert servers.unsupported_settings("ollama", config) == []
    assert servers.unsupported_settings("lmstudio", config) == []


def test_diff_config_ignores_parallel_when_request_left_it_at_default():
    """LM Studio always echoes a ``parallel`` the UI never asked for; the diff
    must not flag it when the request left ``parallel`` untouched (default 1)."""
    requested = LoadConfig(context_length=50432)  # parallel left at default (1)
    served = ServedConfig(context_length=50432, parallel=4)

    diffs = servers.diff_config(requested, served)

    assert all(d.field != "parallel" for d in diffs)


def test_diff_config_reports_parallel_when_request_set_non_default():
    requested = LoadConfig(context_length=50432, parallel=4)
    served = ServedConfig(context_length=50432, parallel=1)

    diffs = servers.diff_config(requested, served)

    fields = {d.field: d for d in diffs}
    assert fields["parallel"].requested == 4
    assert fields["parallel"].served == 1


def test_diff_config_reports_differing_fields_only():
    requested = LoadConfig(
        context_length=65536, flash_attention=True, eval_batch_size=2048
    )
    served = ServedConfig(
        context_length=50432, flash_attention=True, eval_batch_size=512
    )

    diffs = servers.diff_config(requested, served)

    fields = {d.field: d for d in diffs}
    assert "context_length" in fields
    assert fields["context_length"].requested == 65536
    assert fields["context_length"].served == 50432
    assert "eval_batch_size" in fields
    assert "flash_attention" not in fields


def test_ollama_reports_size_and_tools_from_tags(monkeypatch):
    """Seen live on a Jetson (Ollama 0.40.6): /api/tags carries size and capabilities."""
    from harness_lab_app.local_tuning import servers

    tags = {
        "models": [
            {
                "name": "qwen3:8b",
                "model": "qwen3:8b",
                "size": 5225388164,
                "details": {
                    "family": "qwen3",
                    "parameter_size": "8.2B",
                    "quantization_level": "Q4_K_M",
                    "context_length": 40960,
                },
                "capabilities": ["completion", "tools", "thinking"],
            }
        ]
    }

    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/tags"):
            return tags
        if url.endswith("/api/ps"):
            return {"models": []}
        raise servers.ServerError("no show")

    monkeypatch.setattr(servers, "_http", fake_http)
    (model,) = servers.OllamaServer("http://jetson:11434").list_models()
    assert model.size_bytes == 5225388164
    assert model.supports_tools is True
    assert model.max_context == 40960
