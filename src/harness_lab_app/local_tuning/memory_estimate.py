"""Pure memory estimate for *Tune your local model*.

``total = weights + kv_cache + overhead``, split between the GPU/unified pool
and system RAM, compared against the machine's available memory. No I/O: a
single function of ``(ModelInfo, LoadConfig, MachineSpec)``.
"""

from __future__ import annotations

import math

from harness_lab_app.local_tuning.catalog import (
    CONTEXT_OPTIONS,
    active_params_b,
    arch_shape,
    is_moe,
    parse_params_b,
)
from harness_lab_app.local_tuning.contracts import (
    Confidence,
    LoadConfig,
    MachineSpec,
    MemoryBreakdown,
    MemoryEstimate,
    ModelInfo,
    Verdict,
)

# Default bits-per-weight when the server does not report `size_bytes`
# (used to derive weights from the parameter count instead).
_DEFAULT_BITS_PER_WEIGHT = 4.5

# Rough-confidence KV heuristic (no catalogued architecture): per-token bytes
# in f16, derived from the parameter count. Scaled to other KV cache types by
# their byte-width relative to f16 (2 bytes/element).
# Calibrated on GQA models in the catalog: Llama 3.1 8B keeps 0.13 MB/token,
# a dense 27-32B ~0.25. A MoE model's attention follows its *active*
# parameters, with a floor, since a 3B-active model still has a full-width
# attention stack.
_ROUGH_KV_MB_PER_TOKEN_COEFFICIENT = 0.04
_ROUGH_KV_EXPONENT = 0.55
_ROUGH_MOE_FLOOR_PARAMS_B = 8.0
_ROUGH_LOW_FACTOR = 0.5
_ROUGH_HIGH_FACTOR = 1.5

# Overhead: a fixed runtime constant plus a compute buffer that scales with
# the eval batch size and the model size. Flash attention shrinks the compute
# buffer (not the KV cache); without it the buffer is taken 50% larger.
_OVERHEAD_BASE_GB = 0.6
_OVERHEAD_BATCH_COEFFICIENT = 0.35
_OVERHEAD_REFERENCE_BATCH = 512
_OVERHEAD_REFERENCE_PARAMS_B = 8.0
_NO_FLASH_ATTENTION_PENALTY = 0.5

# RAM headroom kept free on top of the reserve, on the system-RAM side of a
# discrete-GPU split.
_RAM_HEADROOM_GB = 2.0

# A configuration is "tight" when the free margin is under this fraction of
# the available memory.
_TIGHT_MARGIN_FRACTION = 0.10


def _params_b(model: ModelInfo) -> float:
    """Best-effort total parameter count in billions, for the overhead term."""
    parsed = parse_params_b(model.params)
    if parsed is not None:
        return parsed
    if model.size_bytes:
        bpw = model.bits_per_weight or _DEFAULT_BITS_PER_WEIGHT
        return (model.size_bytes * 8) / bpw / 1e9
    return _OVERHEAD_REFERENCE_PARAMS_B


def _weights_gb(model: ModelInfo) -> tuple[float, bool]:
    """Weight size in GB, and whether it had to be derived (lowers confidence)."""
    if model.size_bytes:
        return model.size_bytes / 1e9, False
    params_b = parse_params_b(model.params) or _OVERHEAD_REFERENCE_PARAMS_B
    bpw = model.bits_per_weight or _DEFAULT_BITS_PER_WEIGHT
    return params_b * bpw / 8, True


def _effective_context(config: LoadConfig, model: ModelInfo) -> int:
    if model.max_context:
        return min(config.context_length, model.max_context)
    return config.context_length


def _kv_cache_gb(
    model: ModelInfo, config: LoadConfig
) -> tuple[float, Confidence, float, float]:
    """KV cache size, its confidence, and the low/high range (rough only)."""
    shape, confidence = arch_shape(model)
    effective_context = _effective_context(config, model)

    if shape is not None:
        full_bytes_per_token = (
            2
            * shape.kv_heads
            * shape.head_dim
            * config.kv_cache_bytes
            * shape.kv_layers
        )
        # llama.cpp's n_ctx is the total across parallel slots (each slot gets
        # n_ctx / parallel), so the cache holds `context` tokens whatever the
        # slot count. Verified live: LM Studio 0.4 at parallel=4 used 8.9 GB.
        kv_full_gb = full_bytes_per_token * effective_context / 1e9

        kv_swa_gb = 0.0
        if shape.sliding_window and shape.swa_layers:
            swa_bytes_per_token = (
                2
                * shape.kv_heads
                * shape.head_dim
                * config.kv_cache_bytes
                * shape.swa_layers
            )
            swa_tokens = min(effective_context, shape.sliding_window)
            kv_swa_gb = swa_bytes_per_token * swa_tokens / 1e9

        total = kv_full_gb + kv_swa_gb
        return total, confidence, total, total

    params_b = parse_params_b(model.params) or _OVERHEAD_REFERENCE_PARAMS_B
    if model.is_moe or is_moe(model.params):
        active = active_params_b(model.params) or params_b
        params_b = min(params_b, max(active, _ROUGH_MOE_FLOOR_PARAMS_B))
    f16_mb_per_token = _ROUGH_KV_MB_PER_TOKEN_COEFFICIENT * (
        params_b**_ROUGH_KV_EXPONENT
    )
    bytes_per_token = f16_mb_per_token * 1e6 * (config.kv_cache_bytes / 2.0)
    total = bytes_per_token * effective_context / 1e9
    return total, confidence, total * _ROUGH_LOW_FACTOR, total * _ROUGH_HIGH_FACTOR


def _overhead_gb(model: ModelInfo, config: LoadConfig) -> float:
    params_b = _params_b(model)
    batch = config.eval_batch_size or _OVERHEAD_REFERENCE_BATCH
    compute_buffer = (
        _OVERHEAD_BATCH_COEFFICIENT
        * (batch / _OVERHEAD_REFERENCE_BATCH)
        * math.sqrt(params_b / _OVERHEAD_REFERENCE_PARAMS_B)
    )
    if config.flash_attention is False:
        compute_buffer *= 1 + _NO_FLASH_ATTENTION_PENALTY
    return _OVERHEAD_BASE_GB + compute_buffer


def _lever(
    *,
    breakdown: MemoryBreakdown,
    config: LoadConfig,
    machine: MachineSpec,
    verdict: Verdict,
) -> str:
    if verdict == Verdict.ok:
        return ""

    if breakdown.kv_cache_gb > breakdown.weights_gb * 0.25:
        lower_options = [c for c in CONTEXT_OPTIONS if c < config.context_length]
        if lower_options:
            suggested = max(lower_options)
            return (
                f"{config.context_length:,} context adds {breakdown.kv_cache_gb:.1f} GB "
                f"of KV cache; try {suggested:,}"
            )

    if machine.memory_kind == "discrete":
        if config.offload_kv_cache_to_gpu is not False:
            return "keep the KV cache in system RAM"
        if config.gpu_offload >= 1.0:
            return "offload fewer layers to the GPU"

    return "pick a smaller quantization"


def estimate_memory(
    model: ModelInfo, config: LoadConfig, machine: MachineSpec
) -> MemoryEstimate:
    """Estimate whether ``config`` fits on ``machine`` for ``model``."""
    weights_gb, weights_derived = _weights_gb(model)
    kv_cache_gb, confidence, low_total_kv, high_total_kv = _kv_cache_gb(model, config)
    overhead_gb = _overhead_gb(model, config)
    if weights_derived and confidence == Confidence.exact:
        confidence = Confidence.good

    total_gb = weights_gb + kv_cache_gb + overhead_gb
    low_gb = (
        weights_gb + low_total_kv + overhead_gb
        if confidence == Confidence.rough
        else None
    )
    high_gb = (
        weights_gb + high_total_kv + overhead_gb
        if confidence == Confidence.rough
        else None
    )

    notes: list[str] = []

    if machine.memory_kind == "unified":
        available_gb = machine.gpu_gb * machine.gpu_wired_fraction - machine.reserve_gb
        gpu_part = total_gb
        ram_part = 0.0
        ram_overflow = False
    else:
        available_gb = machine.gpu_gb - machine.reserve_gb
        kv_on_gpu = config.offload_kv_cache_to_gpu is not False
        gpu_part = weights_gb * config.gpu_offload + overhead_gb
        ram_part = weights_gb * (1 - config.gpu_offload)
        if kv_on_gpu:
            gpu_part += kv_cache_gb
        else:
            ram_part += kv_cache_gb
        ram_overflow = ram_part > (machine.ram_gb - _RAM_HEADROOM_GB)

    margin = available_gb - gpu_part
    if gpu_part > available_gb or ram_overflow:
        verdict = Verdict.fail
    elif available_gb > 0 and margin < available_gb * _TIGHT_MARGIN_FRACTION:
        verdict = Verdict.tight
    else:
        verdict = Verdict.ok

    # Only macOS caps GPU-wired memory below the pool; a Jetson (fraction 1.0)
    # shares all of it.
    if (
        machine.memory_kind == "unified"
        and machine.gpu_wired_fraction < 1
        and verdict != Verdict.ok
    ):
        notes.append(
            "macOS caps GPU-wired memory at about "
            f"{machine.gpu_wired_fraction:.0%} of RAM by default; raise it with "
            "`iogpu.wired_limit_mb` if this is the binding constraint."
        )

    if confidence == Confidence.rough:
        notes.append(
            "Rough estimate: check Task Manager / Activity Monitor against this."
        )

    breakdown = MemoryBreakdown(
        weights_gb=weights_gb,
        kv_cache_gb=kv_cache_gb,
        overhead_gb=overhead_gb,
        total_gb=total_gb,
        gpu_gb=gpu_part,
        ram_gb=ram_part,
        available_gb=available_gb,
        low_gb=low_gb,
        high_gb=high_gb,
    )

    lever = _lever(breakdown=breakdown, config=config, machine=machine, verdict=verdict)

    return MemoryEstimate(
        breakdown=breakdown,
        verdict=verdict,
        confidence=confidence,
        lever=lever,
        notes=notes,
    )
