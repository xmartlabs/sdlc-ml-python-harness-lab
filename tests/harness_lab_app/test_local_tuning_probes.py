from __future__ import annotations

import pytest

from harness_lab_app.local_tuning import probes
from harness_lab_app.local_tuning.contracts import (
    Completion,
    LoadConfig,
    MachineSpec,
    ModelInfo,
    ServedConfig,
    Verdict,
)


class FakeServer:
    """A LocalServer double returning scripted completions, keyed by prompt size."""

    kind = "lmstudio"
    base_url = "http://localhost:1234"

    def __init__(self, completions=None, by_prompt=None):
        self.completions = completions or []
        self.by_prompt = by_prompt or {}
        self.calls: list[tuple[str, int]] = []

    def list_models(self):
        return []

    def load(self, model_id, config):
        return ServedConfig()

    def unload(self, model_id):
        return None

    def complete(
        self,
        model_id,
        prompt,
        *,
        max_tokens=128,
        tools=None,
        config=None,
        timeout=180.0,
    ):
        self.calls.append((model_id, len(prompt)))
        if self.by_prompt:
            for key, completion in self.by_prompt.items():
                if key in prompt:
                    return completion
        if self.completions:
            return self.completions.pop(0)
        return Completion(text="OK")


def _machine() -> MachineSpec:
    return MachineSpec(memory_kind="discrete", gpu_gb=24, ram_gb=64, reserve_gb=1)


def _config(**overrides) -> LoadConfig:
    data = {"context_length": 32768}
    data.update(overrides)
    return LoadConfig(**data)


def _model(**overrides) -> ModelInfo:
    data = {"id": "prism-ml/bonsai-27b", "size_bytes": int(4.7 * 1024**3)}
    data.update(overrides)
    return ModelInfo(**data)


# ---- filler_prompt ---------------------------------------------------------


def test_filler_prompt_ends_with_reply_marker():
    prompt = probes.filler_prompt(100)
    assert prompt.endswith("Reply with OK.")


def test_filler_prompts_never_share_a_prefix():
    """llama.cpp reuses a cached prompt prefix: a repeated or extended prompt read
    at 113,000 tok/s live (2026-11-09) against ~1,180 for a fresh one."""
    first, second = probes.filler_prompt(500), probes.filler_prompt(500)
    assert first != second
    assert first[:20] != second[:20]


# ---- run_speed --------------------------------------------------------------


def test_run_speed_caps_sizes_at_served_window():
    server = FakeServer(
        completions=[
            Completion(
                prompt_tokens=n,
                time_to_first_token_s=0.5,
                prompt_tokens_per_second=1000.0,
                tokens_per_second=50.0,
            )
            for n in probes.SPEED_SIZES
        ]
    )
    served = ServedConfig(context_length=9000)
    result = probes.run_speed(
        server, "bonsai-27b", _config(context_length=32768), served
    )
    sizes = [p["prompt_tokens"] for p in result.detail["points"]]
    # served window - 1024 = 8976, so 16384 and above must be dropped.
    assert all(size <= 8976 for size in sizes)
    assert sizes == sorted(sizes)


def test_run_speed_verdict_ok_fast_enough():
    server = FakeServer(
        completions=[
            Completion(
                prompt_tokens=n,
                time_to_first_token_s=0.1,
                prompt_tokens_per_second=10000.0,
                tokens_per_second=80.0,
            )
            for n in probes.SPEED_SIZES
        ]
    )
    served = ServedConfig(context_length=32768)
    result = probes.run_speed(server, "m", _config(), served)
    assert result.verdict == Verdict.ok
    assert "agent step" in result.reading


def test_run_speed_verdict_fail_when_slow():
    server = FakeServer(
        completions=[
            Completion(
                prompt_tokens=n,
                time_to_first_token_s=5.0,
                prompt_tokens_per_second=200.0,
                tokens_per_second=10.0,
            )
            for n in probes.SPEED_SIZES
        ]
    )
    served = ServedConfig(context_length=32768)
    result = probes.run_speed(server, "m", _config(), served)
    assert result.verdict == Verdict.fail


def test_run_speed_inconclusive_when_every_completion_errors():
    server = FakeServer(
        completions=[Completion(error="boom") for _ in probes.SPEED_SIZES]
    )
    served = ServedConfig(context_length=32768)
    result = probes.run_speed(server, "m", _config(), served)
    assert result.verdict == Verdict.inconclusive


class _ConfigCapturingServer:
    """A LocalServer double that records the ``config`` every ``complete`` got.

    Guards against the completion silently dropping back to a server's
    default context (e.g. Ollama's 4k ``num_ctx``) when ``config`` is omitted.
    """

    kind = "lmstudio"
    base_url = "http://localhost:1234"

    def __init__(self) -> None:
        self.configs: list[LoadConfig | None] = []

    def list_models(self):
        return []

    def load(self, model_id, config):
        return ServedConfig()

    def unload(self, model_id):
        return None

    def complete(
        self,
        model_id,
        prompt,
        *,
        max_tokens=128,
        tools=None,
        config=None,
        timeout=180.0,
    ):
        self.configs.append(config)
        return Completion(
            text="OK",
            prompt_tokens=10,
            time_to_first_token_s=0.1,
            prompt_tokens_per_second=500.0,
            tokens_per_second=50.0,
        )


def test_run_window_passes_served_context_as_config():
    server = _ConfigCapturingServer()
    served = ServedConfig(context_length=65536)
    probes.run_window(server, "m", _config(context_length=8192), served)
    assert len(server.configs) == 1
    assert server.configs[0] is not None
    assert server.configs[0].context_length == 65536


def test_run_window_passes_requested_config_when_not_served():
    server = _ConfigCapturingServer()
    probes.run_window(server, "m", _config(context_length=8192), None)
    assert server.configs[0] is not None
    assert server.configs[0].context_length == 8192


def test_run_agent_passes_served_context_as_config():
    server = _ConfigCapturingServer()
    served = ServedConfig(context_length=65536)
    model = _model(supports_tools=True)
    probes.run_agent(server, "m", model, _config(context_length=8192), served)
    assert server.configs
    assert all(c is not None and c.context_length == 65536 for c in server.configs)


def test_run_speed_passes_served_context_as_config():
    server = _ConfigCapturingServer()
    served = ServedConfig(context_length=65536)
    probes.run_speed(server, "m", _config(context_length=8192), served)
    assert server.configs
    assert all(c is not None and c.context_length == 65536 for c in server.configs)


# ---- run_window ---------------------------------------------------------


def test_run_window_fail_below_32k():
    server = FakeServer(completions=[Completion(text="OK")])
    served = ServedConfig(context_length=16384)
    result = probes.run_window(server, "m", _config(), served)
    assert result.verdict == Verdict.fail


def test_run_window_tight_below_64k():
    server = FakeServer(completions=[Completion(text="OK")])
    served = ServedConfig(context_length=40000)
    result = probes.run_window(server, "m", _config(), served)
    assert result.verdict == Verdict.tight


def test_run_window_ok_at_or_above_64k():
    server = FakeServer(completions=[Completion(text="OK")])
    served = ServedConfig(context_length=65536)
    result = probes.run_window(server, "m", _config(), served)
    assert result.verdict == Verdict.ok


def test_run_window_fail_on_context_error():
    server = FakeServer(
        completions=[Completion(error="n_ctx exceeded, context length too small")]
    )
    served = ServedConfig(context_length=65536)
    result = probes.run_window(server, "m", _config(), served)
    assert result.verdict == Verdict.fail


# ---- run_agent ------------------------------------------------------------


def test_run_agent_not_applicable_without_tool_support():
    model = _model(supports_tools=False)
    server = FakeServer()
    result = probes.run_agent(server, "m", model, _config())
    assert result.verdict == Verdict.not_applicable


def test_run_agent_all_pass():
    server = FakeServer(
        by_prompt={
            "read_file tool to read notes.txt": Completion(
                tool_calls=[{"name": "read_file", "arguments": {"path": "notes.txt"}}]
            ),
            "write_file tool": Completion(
                tool_calls=[
                    {
                        "name": "write_file",
                        "arguments": {"path": "hello.py", "content": "print('hi')"},
                    }
                ]
            ),
            "2+2": Completion(text="4"),
        }
    )
    model = _model(supports_tools=True)
    result = probes.run_agent(server, "m", model, _config())
    assert result.verdict == Verdict.ok
    assert all(case["failure"] == "" for case in result.detail["cases"])


def test_run_agent_unknown_tool():
    server = FakeServer(
        by_prompt={
            "read_file tool to read notes.txt": Completion(
                tool_calls=[{"name": "delete_file", "arguments": {"path": "notes.txt"}}]
            ),
            "write_file tool": Completion(
                tool_calls=[
                    {
                        "name": "write_file",
                        "arguments": {"path": "hello.py", "content": "print('hi')"},
                    }
                ]
            ),
            "2+2": Completion(text="4"),
        }
    )
    model = _model(supports_tools=True)
    result = probes.run_agent(server, "m", model, _config())
    failures = [case["failure"] for case in result.detail["cases"]]
    assert "unknown_tool" in failures


def test_run_agent_malformed_arguments():
    server = FakeServer(
        by_prompt={
            "read_file tool to read notes.txt": Completion(
                tool_calls=[{"name": "read_file", "arguments": {}}]
            ),
            "write_file tool": Completion(
                tool_calls=[
                    {
                        "name": "write_file",
                        "arguments": {"path": "hello.py", "content": "print('hi')"},
                    }
                ]
            ),
            "2+2": Completion(text="4"),
        }
    )
    model = _model(supports_tools=True)
    result = probes.run_agent(server, "m", model, _config())
    failures = [case["failure"] for case in result.detail["cases"]]
    assert "malformed_arguments" in failures


def test_run_agent_spurious_call():
    server = FakeServer(
        by_prompt={
            "read_file tool to read notes.txt": Completion(
                tool_calls=[{"name": "read_file", "arguments": {"path": "notes.txt"}}]
            ),
            "write_file tool": Completion(
                tool_calls=[
                    {
                        "name": "write_file",
                        "arguments": {"path": "hello.py", "content": "print('hi')"},
                    }
                ]
            ),
            "2+2": Completion(
                tool_calls=[{"name": "read_file", "arguments": {"path": "x"}}]
            ),
        }
    )
    model = _model(supports_tools=True)
    result = probes.run_agent(server, "m", model, _config())
    failures = [case["failure"] for case in result.detail["cases"]]
    assert "spurious_call" in failures
    assert result.verdict == Verdict.tight


def test_run_agent_no_call():
    server = FakeServer(
        by_prompt={
            "read_file tool to read notes.txt": Completion(text="sorry, no"),
            "write_file tool": Completion(
                tool_calls=[
                    {
                        "name": "write_file",
                        "arguments": {"path": "hello.py", "content": "print('hi')"},
                    }
                ]
            ),
            "2+2": Completion(text="4"),
        }
    )
    model = _model(supports_tools=True)
    result = probes.run_agent(server, "m", model, _config())
    failures = [case["failure"] for case in result.detail["cases"]]
    assert "no_call" in failures


def test_run_agent_inconclusive_on_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("server exploded")

    server = FakeServer()
    monkeypatch.setattr(server, "complete", boom)
    model = _model(supports_tools=True)
    result = probes.run_agent(server, "m", model, _config())
    assert result.verdict == Verdict.inconclusive


# ---- run_fit (requires memory_estimate; skipped if that module is absent) --


def test_run_fit_inconclusive_on_exception(monkeypatch):
    memory_estimate_mod = pytest.importorskip(
        "harness_lab_app.local_tuning.memory_estimate"
    )

    def boom(*args, **kwargs):
        raise RuntimeError("no estimate")

    monkeypatch.setattr(memory_estimate_mod, "estimate_memory", boom)
    result = probes.run_fit(_model(), _config(), _machine(), None)
    assert result.verdict == Verdict.inconclusive


def test_speed_points_count_the_tokens_the_server_read():
    """The token count the server reports wins over the size that was asked for."""

    class _Server:
        kind = "lmstudio"
        base_url = "http://h:1234"

        def complete(self, model_id, prompt, **kwargs):
            return Completion(
                text="OK",
                prompt_tokens=3000,
                time_to_first_token_s=2.0,
                prompt_tokens_per_second=1500.0,
                tokens_per_second=70.0,
            )

    result = probes.run_speed(
        _Server(),
        "m",
        LoadConfig(context_length=8192),
        ServedConfig(context_length=8192),
    )
    assert {p["prompt_tokens"] for p in result.detail["points"]} == {3000}


def test_a_model_with_nothing_in_vram_is_reported_as_running_on_cpu():
    """Ollama on the Jetson served qwen3:8b with size_vram 0 even at 8k context."""
    model = ModelInfo(
        id="qwen3:8b", arch="qwen3", params="8.2B", size_bytes=5_225_388_164
    )
    machine = MachineSpec(
        memory_kind="unified",
        gpu_gb=8,
        ram_gb=8,
        reserve_gb=1.5,
        gpu_wired_fraction=1.0,
    )
    result = probes.run_fit(
        model,
        LoadConfig(context_length=8192),
        machine,
        ServedConfig(context_length=8192, vram_bytes=0),
    )
    assert result.verdict == "fail"
    assert result.detail["cpu_only"] is True
    assert "CPU" in result.reading
