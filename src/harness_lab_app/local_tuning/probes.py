"""The four quick tests of *Tune your local model* (spec 2026-11-08).

Pure application logic over the :class:`~harness_lab_app.local_tuning.contracts.
LocalServer` protocol: every function here takes a server (or a fake
implementing the same protocol in tests) and returns a typed
:class:`~harness_lab_app.local_tuning.contracts.TestResult` with a
plain-language reading for the UI. Nothing here loads or unloads a model —
that is ``servers.py``'s job, called from the API layer.

``memory_estimate`` is imported lazily inside :func:`run_fit` because it is
owned by a parallel work stream; importing it at module import time would
make this module fail to import before that stream lands.
"""

from __future__ import annotations

import itertools
import uuid

from harness_lab_app.capability import classify_failure_text
from harness_lab_app.local_tuning.contracts import (
    Completion,
    Confidence,
    LoadConfig,
    MachineSpec,
    ModelInfo,
    ServedConfig,
    SpeedPoint,
    TestResult,
    Verdict,
)
from harness_lab_app.local_tuning.headroom import write_headroom

# The harness system prompt (governance docs, skill descriptions, agent
# definitions and tool schemas) is ~14k tokens before the first user turn
# (see gateway_models.MIN_CONTEXT_TOKENS's docstring for the measurement).
HARNESS_PROMPT_TOKENS = 14000

# Prompt sizes for the speed test, before the configured/served window is
# appended and the whole set is capped (spec "Tests" #2).
SPEED_SIZES = (2048, 8192, 16384)

# Below the harness window floor (gateway_models.MIN_CONTEXT_TOKENS) a run
# fails on the first multi-step prompt; below the agentic floor
# (gateway_models.AGENTIC_CONTEXT_TOKENS) it starts but runs out mid-task.
_WINDOW_FAIL_TOKENS = 32768
_WINDOW_TIGHT_TOKENS = 65536

# A word list rather than one repeated token: repetitive text compresses to
# far fewer tokens than natural prose, which would undershoot the prompt size
# the caller asked for. ~4 chars/token, matching the spec's estimate.
_FILLER = (
    "the quick brown fox jumps over a lazy dog while seventeen bright copper "
    "kettles hum beside the harbour and every navigator counts another mile "
    "before dawn breaks over the quiet valley below "
)
_CHARS_PER_TOKEN = 4
_REPLY_SUFFIX = "Reply with OK."


def filler_prompt(tokens: int) -> str:
    """Filler text of approximately ``tokens`` tokens that no cache can reuse.

    Each prompt opens with a fresh random tag. llama.cpp (LM Studio, Ollama)
    reuses the longest cached prefix of a new prompt, so a deterministic filler
    made every larger size an extension of the previous one: only the new tail
    was read, and prompt speed came out inflated. Measured live on 2026-11-09:
    a repeated 17k-token prompt "read" at 113,000 tok/s, a fresh one at ~1,180.

    Always ends with "Reply with OK." so a model that reads the whole prompt
    has something unambiguous to answer.
    """
    tokens = max(0, tokens)
    tag = f"[probe {uuid.uuid4().hex}]"
    body_chars = max(0, tokens * _CHARS_PER_TOKEN - len(_REPLY_SUFFIX) - len(tag) - 2)
    if body_chars <= 0:
        return f"{tag} {_REPLY_SUFFIX}"
    repeats = body_chars // len(_FILLER) + 1
    body = (_FILLER * repeats)[:body_chars]
    return f"{tag} {body} {_REPLY_SUFFIX}"


def _effective_load_config(
    config: LoadConfig, served: ServedConfig | None
) -> LoadConfig:
    """``config`` with every field the server reported back actually applied.

    After loading, the served configuration is what the estimate and the
    tests must reason about from then on (spec "Error handling": "served
    configuration differs from the request ... drives the estimate and the
    tests from then on").
    """
    if served is None:
        return config
    data = config.model_dump()
    if served.context_length is not None:
        data["context_length"] = served.context_length
    if served.gpu_offload is not None:
        data["gpu_offload"] = served.gpu_offload
    if served.flash_attention is not None:
        data["flash_attention"] = served.flash_attention
    if served.offload_kv_cache_to_gpu is not None:
        data["offload_kv_cache_to_gpu"] = served.offload_kv_cache_to_gpu
    if served.eval_batch_size is not None:
        data["eval_batch_size"] = served.eval_batch_size
    if served.num_experts is not None:
        data["num_experts"] = served.num_experts
    if served.parallel is not None:
        data["parallel"] = served.parallel
    return LoadConfig(**data)


def run_fit(
    model: ModelInfo,
    config: LoadConfig,
    machine: MachineSpec,
    served: ServedConfig | None,
) -> TestResult:
    """The memory estimate, read back against the served configuration.

    Before loading, ``served`` is ``None`` and the estimate is purely the
    request; after loading, the served values (when the server reported them)
    replace the requested ones, per the spec's "Fit" test.
    """
    try:
        from harness_lab_app.local_tuning import memory_estimate as memory_estimate_mod

        effective_config = _effective_load_config(config, served)
        estimate = memory_estimate_mod.estimate_memory(model, effective_config, machine)
        breakdown = estimate.breakdown
        confidence_label = {
            Confidence.exact: "exact",
            Confidence.good: "good",
            Confidence.rough: "rough",
        }.get(estimate.confidence, str(estimate.confidence))
        verb = {
            Verdict.ok: "Fits",
            Verdict.tight: "Tight fit",
            Verdict.fail: "Does not fit",
        }.get(estimate.verdict, "Fits")
        reading = (
            f"{verb}: ~{breakdown.total_gb:.1f} GB of {breakdown.available_gb:.0f} GB "
            f"available ({confidence_label} confidence)"
        )
        if estimate.lever:
            reading = f"{reading}. {estimate.lever}"
        if served is not None and served.vram_bytes == 0:
            # Ollama reports where the runner put the model. 0 bytes in VRAM is a
            # CPU-only load: seen live on a Jetson Orin Nano (2026-11-09), where
            # even an 8k-context 8B model that fits ran on the CPU.
            return TestResult(
                name="fit",
                verdict=Verdict.fail,
                reading=(
                    "The model is running on the CPU: the server reports 0 bytes in "
                    "GPU memory. If the estimate fits, the server is not using the "
                    "GPU at all: check that Ollama was built with CUDA (on a Jetson, "
                    "the JetPack build or a container started with "
                    "`--runtime nvidia`) and run `ollama ps` on the device; its "
                    "PROCESSOR column should read GPU. " + reading
                ),
                detail={"estimate": estimate.model_dump(), "cpu_only": True},
            )
        return TestResult(
            name="fit",
            verdict=estimate.verdict,
            reading=reading,
            detail={"estimate": estimate.model_dump()},
        )
    except Exception as exc:
        return TestResult(
            name="fit",
            verdict=Verdict.inconclusive,
            reading="Could not estimate memory for this configuration.",
            detail={"error": str(exc)},
        )


def _interpolate(
    points: list[SpeedPoint], target_tokens: int, field: str
) -> float | None:
    """Linear interpolation of ``field`` over ``prompt_tokens``, clamped at the ends."""
    samples = sorted(
        (p.prompt_tokens, getattr(p, field))
        for p in points
        if getattr(p, field) is not None
    )
    if not samples:
        return None
    if len(samples) == 1 or target_tokens <= samples[0][0]:
        return samples[0][1]
    if target_tokens >= samples[-1][0]:
        return samples[-1][1]
    for (x0, y0), (x1, y1) in itertools.pairwise(samples):
        if x0 <= target_tokens <= x1:
            if x1 == x0:
                return y0
            ratio = (target_tokens - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return samples[-1][1]


def run_speed(
    server,
    model_id: str,
    config: LoadConfig,
    served: ServedConfig | None,
    *,
    model: ModelInfo | None = None,
    bandwidth_gbs: float | None = None,
) -> TestResult:
    """Completion throughput at several prompt sizes, read as agent-step cost."""
    try:
        effective_config = _effective_load_config(config, served)
        served_context = served.context_length if served is not None else None
        window = served_context or config.context_length
        candidates = sorted({*SPEED_SIZES, config.context_length})
        cap = (served_context - 1024) if served_context else None
        if cap is not None:
            candidates = [size for size in candidates if size <= cap]
        sizes = sorted({size for size in candidates if size <= window})

        points: list[SpeedPoint] = []
        errors: list[str] = []
        for size in sizes:
            prompt = filler_prompt(size)
            completion: Completion = server.complete(
                model_id, prompt, max_tokens=128, config=effective_config
            )
            if completion.error:
                errors.append(completion.error)
                points.append(
                    SpeedPoint(
                        prompt_tokens=size,
                        time_to_first_token_s=None,
                        prompt_tokens_per_second=None,
                        tokens_per_second=None,
                    )
                )
                continue
            points.append(
                SpeedPoint(
                    # What the server read, not what was asked for: the
                    # tokenizer decides how many tokens the filler becomes.
                    prompt_tokens=completion.prompt_tokens or size,
                    time_to_first_token_s=completion.time_to_first_token_s,
                    prompt_tokens_per_second=completion.prompt_tokens_per_second,
                    tokens_per_second=completion.tokens_per_second,
                )
            )

        read_rate = _interpolate(
            points, HARNESS_PROMPT_TOKENS, "prompt_tokens_per_second"
        )
        write_rate = _interpolate(points, HARNESS_PROMPT_TOKENS, "tokens_per_second")
        if not sizes or read_rate is None or read_rate <= 0:
            return TestResult(
                name="speed",
                verdict=Verdict.inconclusive,
                reading="Every completion errored; speed could not be measured.",
                detail={"points": [p.model_dump() for p in points], "errors": errors},
            )

        seconds_per_step = HARNESS_PROMPT_TOKENS / read_rate
        if seconds_per_step < 20:
            verdict = Verdict.ok
        elif seconds_per_step < 60:
            verdict = Verdict.tight
        else:
            verdict = Verdict.fail

        total_minutes = 90 * seconds_per_step / 60
        low = total_minutes * 0.85
        high = total_minutes * 1.15
        write_label = f"{write_rate:.0f}" if write_rate is not None else "n/a"
        reading = (
            f"Reads ~{read_rate:.0f} tok/s, writes ~{write_label} tok/s. Each agent "
            f"step spends ~{seconds_per_step:.1f} s reading the ~14k-token harness "
            f"prompt; a 90-step run ~= {low:.0f}-{high:.0f} min."
        )
        return TestResult(
            name="speed",
            verdict=verdict,
            reading=reading,
            detail={
                "points": [p.model_dump() for p in points],
                "read_tokens_per_second": read_rate,
                "write_tokens_per_second": write_rate,
                "seconds_per_step": seconds_per_step,
                "errors": errors,
                "headroom": (
                    write_headroom(
                        write_rate,
                        bandwidth_gbs,
                        model.size_bytes,
                        model.params,
                        model.quantization,
                    )
                    if model is not None
                    else None
                ),
            },
        )
    except Exception as exc:
        return TestResult(
            name="speed",
            verdict=Verdict.inconclusive,
            reading="The speed test could not run.",
            detail={"error": str(exc)},
        )


def run_window(
    server, model_id: str, config: LoadConfig, served: ServedConfig | None
) -> TestResult:
    """Confirm the served window actually holds the harness prompt (spec "Window").

    ``config`` (overridden with the served context length when known) is
    passed to ``complete`` so Ollama's request carries ``num_ctx`` for the
    served window rather than silently falling back to its 4k default.
    """
    try:
        from harness_lab_app.gateway_models import MIN_CONTEXT_TOKENS

        served_context = served.context_length if served is not None else None
        effective_config = _effective_load_config(config, served)
        prompt = filler_prompt(MIN_CONTEXT_TOKENS - 1024)
        completion = server.complete(
            model_id, prompt, max_tokens=16, config=effective_config
        )
        context_error = (
            bool(completion.error)
            and classify_failure_text(completion.error) is not None
        )

        if served_context is None and not context_error:
            return TestResult(
                name="window",
                verdict=Verdict.inconclusive,
                reading="The server did not report a served context length.",
                detail={"completion_error": completion.error},
            )

        if context_error or (
            served_context is not None and served_context < _WINDOW_FAIL_TOKENS
        ):
            verdict = Verdict.fail
        elif served_context is not None and served_context < _WINDOW_TIGHT_TOKENS:
            verdict = Verdict.tight
        else:
            verdict = Verdict.ok

        if context_error:
            reading = (
                f"The host answered with a context-length error around "
                f"{MIN_CONTEXT_TOKENS - 1024} tokens of prompt."
            )
        else:
            reading = f"Serving {served_context} tokens of context."
            if verdict == Verdict.fail:
                reading += " Below the 32k floor a multi-step run fails outright."
            elif verdict == Verdict.tight:
                reading += " Below 64k an agentic run tends to run out mid-task."

        return TestResult(
            name="window",
            verdict=verdict,
            reading=reading,
            detail={
                "served_context": served_context,
                "completion_error": completion.error,
            },
        )
    except Exception as exc:
        return TestResult(
            name="window",
            verdict=Verdict.inconclusive,
            reading="The window test could not run.",
            detail={"error": str(exc)},
        )


_READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read the content of a file at the given path.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}

_WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write content to a file at the given path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
}

_AGENT_TOOLS = [_READ_FILE_TOOL, _WRITE_FILE_TOOL]

_AGENT_CASES = (
    {
        "prompt": "Use the read_file tool to read notes.txt.",
        "expected": "read_file",
        "required": ("path",),
    },
    {
        "prompt": "Create hello.py containing print('hi') using the write_file tool.",
        "expected": "write_file",
        "required": ("path", "content"),
    },
    {
        "prompt": "What is 2+2? Answer without using tools.",
        "expected": "none",
        "required": (),
    },
)

_DECLARED_TOOLS = {"read_file", "write_file"}


def _classify_agent_case(case: dict, completion: Completion) -> tuple[str, str]:
    """``(got, failure)`` for one agent-test case; ``failure`` empty on success."""
    calls = completion.tool_calls
    expected = case["expected"]

    if expected == "none":
        if calls:
            got = calls[0].get("name", "unknown")
            return got, "spurious_call"
        return "none", ""

    if not calls:
        return "none", "no_call"

    call = calls[0]
    name = call.get("name")
    if name not in _DECLARED_TOOLS:
        return str(name), "unknown_tool"
    if name != expected:
        return str(name), "unknown_tool"

    arguments = call.get("arguments")
    if not isinstance(arguments, dict) or "_invalid_json" in arguments:
        return str(name), "malformed_arguments"
    if any(key not in arguments for key in case["required"]):
        return str(name), "malformed_arguments"
    return str(name), ""


def run_agent(
    server,
    model_id: str,
    model: ModelInfo,
    config: LoadConfig,
    served: ServedConfig | None = None,
) -> TestResult:
    """Three short tool-calling probes: two expected calls, one expected refusal."""
    if model.supports_tools is False:
        return TestResult(
            name="agent",
            verdict=Verdict.not_applicable,
            reading="This model does not report tool-use support, so the agent "
            "check does not apply.",
            detail={},
        )

    try:
        effective_config = _effective_load_config(config, served)
        cases_detail = []
        passed = 0
        for case in _AGENT_CASES:
            completion = server.complete(
                model_id,
                case["prompt"],
                max_tokens=1024,
                tools=_AGENT_TOOLS,
                config=effective_config,
            )
            if completion.error:
                got, failure = "none", "no_call"
            else:
                got, failure = _classify_agent_case(case, completion)
            if not failure:
                passed += 1
            cases_detail.append(
                {
                    "prompt": case["prompt"],
                    "expected": case["expected"],
                    "got": got,
                    "failure": failure,
                }
            )

        if passed == 3:
            verdict = Verdict.ok
        elif passed == 2:
            verdict = Verdict.tight
        else:
            verdict = Verdict.fail

        reading = f"Tool calls: {passed}/3 cases passed."
        return TestResult(
            name="agent",
            verdict=verdict,
            reading=reading,
            detail={"cases": cases_detail},
        )
    except Exception as exc:
        return TestResult(
            name="agent",
            verdict=Verdict.inconclusive,
            reading="The agent test could not run.",
            detail={"error": str(exc)},
        )
