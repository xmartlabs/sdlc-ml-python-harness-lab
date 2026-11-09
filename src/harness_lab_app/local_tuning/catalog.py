"""Reference data for *Tune your local model*: no I/O, no framework logic.

Reference machines, the parameter-option lists and defaults, and the
architecture table used to find `layers / kv_heads / head_dim` when the
server itself does not report them (LM Studio). Every ``ARCH_TABLE`` entry
carries a ``# source:`` comment naming where its numbers come from.
"""

from __future__ import annotations

import re

from harness_lab_app.local_tuning.contracts import (
    ArchShape,
    Confidence,
    LoadConfig,
    ModelInfo,
)

# --- Reference machines ---------------------------------------------------
# Unified memory (Apple silicon): ram_gb == gpu_gb, reserve 4 GB for the OS.
# Discrete GPU + system RAM: reserve 1 GB of VRAM for the desktop/driver.

# bandwidth_gbs: memory bandwidth, the ceiling on token generation. Vendor
# specifications; Apple figures as cited in ggml-org/llama.cpp discussion #4167.
# Mac Studio assumes an M2/M3 Ultra (800 GB/s). 0 = unknown.
REFERENCE_MACHINES: list[dict] = [
    {
        "id": "macbook-air-m1-8",
        "label": "MacBook Air M1 · 8 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 8.0,
        "ram_gb": 8.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 68.0,
    },
    {
        "id": "macbook-air-m1-16",
        "label": "MacBook Air M1 · 16 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 16.0,
        "ram_gb": 16.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 68.0,
    },
    {
        "id": "macbook-pro-m2-16",
        "label": "MacBook Pro M2 · 16 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 16.0,
        "ram_gb": 16.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 100.0,
    },
    {
        "id": "macbook-pro-m3-24",
        "label": "MacBook Pro M3 · 24 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 24.0,
        "ram_gb": 24.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 100.0,
    },
    {
        "id": "macbook-pro-m3-pro-18",
        "label": "MacBook Pro M3 Pro · 18 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 18.0,
        "ram_gb": 18.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 150.0,
    },
    {
        "id": "macbook-pro-m3-pro-36",
        "label": "MacBook Pro M3 Pro · 36 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 36.0,
        "ram_gb": 36.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 150.0,
    },
    {
        "id": "macbook-pro-m3-max-36",
        "label": "MacBook Pro M3 Max · 36 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 36.0,
        "ram_gb": 36.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 300.0,
    },
    {
        "id": "macbook-pro-m4-max-48",
        "label": "MacBook Pro M4 Max · 48 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 48.0,
        "ram_gb": 48.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 546.0,
    },
    {
        "id": "macbook-pro-m4-max-64",
        "label": "MacBook Pro M4 Max · 64 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 64.0,
        "ram_gb": 64.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 546.0,
    },
    {
        "id": "macbook-pro-m4-max-128",
        "label": "MacBook Pro M4 Max · 128 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 128.0,
        "ram_gb": 128.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 546.0,
    },
    {
        "id": "mac-studio-96",
        "label": "Mac Studio · 96 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 96.0,
        "ram_gb": 96.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 800.0,
    },
    {
        "id": "mac-studio-192",
        "label": "Mac Studio · 192 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 192.0,
        "ram_gb": 192.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 800.0,
    },
    {
        "id": "pc-rtx3060-12-32",
        "label": "PC · RTX 3060 12 GB + 32 GB RAM",
        "memory_kind": "discrete",
        "gpu_gb": 12.0,
        "ram_gb": 32.0,
        "reserve_gb": 1.0,
        "bandwidth_gbs": 360.0,
    },
    {
        "id": "pc-rtx4060ti-16-32",
        "label": "PC · RTX 4060 Ti 16 GB + 32 GB RAM",
        "memory_kind": "discrete",
        "gpu_gb": 16.0,
        "ram_gb": 32.0,
        "reserve_gb": 1.0,
        "bandwidth_gbs": 288.0,
    },
    {
        "id": "pc-rtx3090-24-32",
        "label": "PC · RTX 3090 24 GB + 32 GB RAM",
        "memory_kind": "discrete",
        "gpu_gb": 24.0,
        "ram_gb": 32.0,
        "reserve_gb": 1.0,
        "bandwidth_gbs": 936.0,
    },
    {
        "id": "pc-rtx4090-24-64",
        "label": "PC · RTX 4090 24 GB + 64 GB RAM",
        "memory_kind": "discrete",
        "gpu_gb": 24.0,
        "ram_gb": 64.0,
        "reserve_gb": 1.0,
        "bandwidth_gbs": 1008.0,
    },
    {
        "id": "pc-rtx5090-32-64",
        "label": "PC · RTX 5090 32 GB + 64 GB RAM",
        "memory_kind": "discrete",
        "gpu_gb": 32.0,
        "ram_gb": 64.0,
        "reserve_gb": 1.0,
        "bandwidth_gbs": 1792.0,
    },
    # NVIDIA Jetson: 8 GB LPDDR5 shared by CPU and GPU, like Apple's unified
    # memory. Bandwidth per NVIDIA: 68 GB/s originally, 102 GB/s in the
    # "Super" MAXN mode (JetPack 6.1+). Reserve covers JetPack/Ubuntu.
    {
        "id": "jetson-orin-nano-super-8",
        "label": "NVIDIA Jetson Orin Nano Super · 8 GB shared (MAXN)",
        "memory_kind": "unified",
        "gpu_gb": 8.0,
        "ram_gb": 8.0,
        "reserve_gb": 1.5,
        "bandwidth_gbs": 102.0,
        "gpu_wired_fraction": 1.0,
    },
    {
        "id": "jetson-orin-nano-8",
        "label": "NVIDIA Jetson Orin Nano · 8 GB shared (original clocks)",
        "memory_kind": "unified",
        "gpu_gb": 8.0,
        "ram_gb": 8.0,
        "reserve_gb": 1.5,
        "bandwidth_gbs": 68.0,
        "gpu_wired_fraction": 1.0,
    },
    # Desktop Macs (Apple spec pages / support archive, retrieved 2026-11-09).
    {
        "id": "mac-mini-m4-16",
        "label": "Mac mini M4 · 16 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 16.0,
        "ram_gb": 16.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 120.0,
    },
    {
        "id": "mac-mini-m4-32",
        "label": "Mac mini M4 · 32 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 32.0,
        "ram_gb": 32.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 120.0,
    },
    {
        "id": "mac-mini-m4-pro-64",
        "label": "Mac mini M4 Pro · 64 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 64.0,
        "ram_gb": 64.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 273.0,
    },
    {
        "id": "mac-mini-m5-pro-64",
        "label": "Mac mini M5 Pro · 64 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 64.0,
        "ram_gb": 64.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 307.0,
    },
    {
        "id": "mac-mini-m6-32",
        "label": "Mac mini M6 · 32 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 32.0,
        "ram_gb": 32.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 170.0,
    },
    {
        "id": "mac-studio-m4-max-128",
        "label": "Mac Studio M4 Max (40-core GPU) · 128 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 128.0,
        "ram_gb": 128.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 546.0,
    },
    {
        "id": "mac-studio-m3-ultra-256",
        "label": "Mac Studio M3 Ultra · 256 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 256.0,
        "ram_gb": 256.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 819.0,
    },
    {
        "id": "mac-studio-m5-max-128",
        "label": "Mac Studio M5 Max (40-core GPU) · 128 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 128.0,
        "ram_gb": 128.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 614.0,
    },
    {
        "id": "mac-studio-m5-ultra-512",
        "label": "Mac Studio M5 Ultra · 512 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 512.0,
        "ram_gb": 512.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 1228.0,
    },
    # NVIDIA DGX Spark (GB10): 128 GB coherent LPDDR5x, 273 GB/s (NVIDIA spec).
    {
        "id": "dgx-spark-128",
        "label": "NVIDIA DGX Spark (GB10) · 128 GB unified",
        "memory_kind": "unified",
        "gpu_gb": 128.0,
        "ram_gb": 128.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 273.0,
        "gpu_wired_fraction": 1.0,
    },
    # AMD Ryzen AI Max+ 395 (Strix Halo): 256-bit LPDDR5x-8000, 256 GB/s (AMD).
    # The GPU may use ~96 GB on Windows (VGM) and ~120 GB on Linux (TTM limit).
    {
        "id": "ryzen-ai-max-395-128-linux",
        "label": "AMD Ryzen AI Max+ 395 · 128 GB (Linux, ~120 GB to GPU)",
        "memory_kind": "unified",
        "gpu_gb": 128.0,
        "ram_gb": 128.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 256.0,
        "gpu_wired_fraction": 0.94,
    },
    {
        "id": "ryzen-ai-max-395-128-windows",
        "label": "AMD Ryzen AI Max+ 395 · 128 GB (Windows, 96 GB to GPU)",
        "memory_kind": "unified",
        "gpu_gb": 128.0,
        "ram_gb": 128.0,
        "reserve_gb": 4.0,
        "bandwidth_gbs": 256.0,
        "gpu_wired_fraction": 0.75,
    },
    {
        "id": "other",
        "label": "Other…",
        "memory_kind": "discrete",
        "gpu_gb": 0.0,
        "ram_gb": 0.0,
        "reserve_gb": 0.0,
        "bandwidth_gbs": 0.0,
    },
]

# --- Parameter options and defaults ---------------------------------------

CONTEXT_OPTIONS: list[int] = [8192, 16384, 32768, 50432, 65536, 131072]
EVAL_BATCH_OPTIONS: list[int] = [256, 512, 1024, 2048]

# Bytes per KV-cache element relative to f16 (2 bytes): q8_0 and q4_0 keep a
# few extra bytes per block for quantization scales, hence > 1/2 and > 1/4.
KV_CACHE_TYPES: dict[str, float] = {
    "f16": 2.0,
    "q8_0": 1.0625,
    "q4_0": 0.5625,
}

DEFAULT_LOAD = LoadConfig(
    context_length=32768,
    gpu_offload=1.0,
    flash_attention=True,
    offload_kv_cache_to_gpu=True,
    eval_batch_size=512,
)

# --- Architecture table ----------------------------------------------------
# Keyed by the `arch` string servers report (lowercase). Each entry maps a
# representative total-parameter count (billions) to the KV-cache shape
# nearest it. `arch_shape` picks the closest bucket for the model at hand.

_LLAMA_BUCKETS = {
    # source: meta-llama/Meta-Llama-3.1-8B config.json
    8.0: {"layers": 32, "kv_layers": 32, "kv_heads": 8, "head_dim": 128},
    # source: meta-llama/Meta-Llama-3.1-70B config.json
    70.0: {"layers": 80, "kv_layers": 80, "kv_heads": 8, "head_dim": 128},
}

_QWEN3_BUCKETS = {
    # source: Qwen/Qwen3-8B config.json
    8.0: {"layers": 36, "kv_layers": 36, "kv_heads": 8, "head_dim": 128},
    # source: Qwen/Qwen3-14B config.json
    14.0: {"layers": 40, "kv_layers": 40, "kv_heads": 8, "head_dim": 128},
    # source: Qwen/Qwen3-30B-A3B config.json (MoE, dense attention shape)
    30.0: {"layers": 48, "kv_layers": 48, "kv_heads": 4, "head_dim": 128},
    # source: Qwen/Qwen3-32B config.json
    32.0: {"layers": 64, "kv_layers": 64, "kv_heads": 8, "head_dim": 128},
}

_QWEN35_BUCKETS = {
    # source: approximation from Qwen3.5/3.6/3.8 preview config.json; hybrid
    # architecture where only every 4th layer keeps a full KV cache — verify
    # against the vendor's released config.json.
    9.0: {"layers": 32, "kv_layers": 8, "kv_heads": 4, "head_dim": 256},
    27.0: {"layers": 64, "kv_layers": 16, "kv_heads": 4, "head_dim": 256},
    35.0: {
        "layers": 40,
        "kv_layers": 10,
        "kv_heads": 2,
        "head_dim": 256,
    },  # 35B-A3B MoE
}

_GEMMA_BUCKETS = {
    # source: approximation from Gemma 3 27B config.json; 5 sliding-window
    # layers per 1 global (full) layer — verify exact global-layer positions.
    27.0: {
        "layers": 62,
        "kv_layers": 11,
        "kv_heads": 16,
        "head_dim": 128,
        "sliding_window": 1024,
        "swa_layers": 51,
    },
}

_GPT_OSS_BUCKETS = {
    # source: openai/gpt-oss-20b config.json; alternating sliding-window /
    # full-attention layers.
    20.0: {
        "layers": 24,
        "kv_layers": 12,
        "kv_heads": 8,
        "head_dim": 64,
        "sliding_window": 128,
        "swa_layers": 12,
    },
}

_MISTRAL_BUCKETS = {
    # source: mistralai/Mistral-7B-v0.3 config.json
    7.0: {"layers": 32, "kv_layers": 32, "kv_heads": 8, "head_dim": 128},
}

ARCH_TABLE: dict[str, dict] = {
    "llama": {"buckets": _LLAMA_BUCKETS},
    "qwen2": {"buckets": _QWEN3_BUCKETS},
    "qwen3": {"buckets": _QWEN3_BUCKETS},
    "qwen3moe": {"buckets": _QWEN3_BUCKETS},
    "qwen35": {"buckets": _QWEN35_BUCKETS},
    # LM Studio 0.4 reports the Qwen3.5+/3.6 MoE models as `qwen35moe`.
    "qwen35moe": {"buckets": _QWEN35_BUCKETS},
    "qwen36": {"buckets": _QWEN35_BUCKETS},
    "qwen38": {"buckets": _QWEN35_BUCKETS},
    "gemma3": {"buckets": _GEMMA_BUCKETS},
    "gemma4": {"buckets": _GEMMA_BUCKETS},
    "gpt-oss": {"buckets": _GPT_OSS_BUCKETS},
    "mistral": {"buckets": _MISTRAL_BUCKETS},
    "mistral3": {"buckets": _MISTRAL_BUCKETS},
}

_MOE_PATTERN = re.compile(r"A\d+(?:\.\d+)?B", re.IGNORECASE)
_EXPERT_MIX_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)B$", re.IGNORECASE)
_TOTAL_WITH_ACTIVE_PATTERN = re.compile(
    r"^(\d+(?:\.\d+)?)B(?:-A\d+(?:\.\d+)?B)?$", re.IGNORECASE
)
_LEADING_NUMBER_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)")


def parse_params_b(params: str) -> float | None:
    """Total parameter count in billions, e.g. "27B" -> 27.0, "8x7B" -> 56.0."""
    if not params:
        return None
    text = params.strip().upper()

    mix_match = _EXPERT_MIX_PATTERN.match(text)
    if mix_match:
        return float(mix_match.group(1)) * float(mix_match.group(2))

    total_match = _TOTAL_WITH_ACTIVE_PATTERN.match(text)
    if total_match:
        return float(total_match.group(1))

    leading_match = _LEADING_NUMBER_PATTERN.match(text)
    if leading_match:
        return float(leading_match.group(1))

    return None


def active_params_b(params: str) -> float | None:
    """Active parameters per token in billions: "35B-A3B" -> 3.0, else the total."""
    if not params:
        return None
    match = re.search(r"A(\d+(?:\.\d+)?)B", params.strip().upper())
    if match:
        return float(match.group(1))
    return parse_params_b(params)


def is_moe(params: str) -> bool:
    """True for mixture-of-experts notations: "30B-A3B" or "8x7B" style."""
    if not params:
        return False
    text = params.strip().upper()
    return bool(_MOE_PATTERN.search(text)) or bool(_EXPERT_MIX_PATTERN.match(text))


def _arch_candidates(arch: str) -> list[str]:
    """The raw arch string, then its trailing-digit/version suffix stripped."""
    normalized = arch.strip().lower()
    candidates = [normalized]
    stripped = re.sub(r"[\d.]+$", "", normalized)
    if stripped and stripped != normalized:
        candidates.append(stripped)
    # "qwen35moe" -> "qwen35": a MoE variant shares its family's attention shape.
    without_moe = re.sub(r"[-_]?moe$", "", normalized)
    if without_moe and without_moe not in candidates:
        candidates.append(without_moe)
    return candidates


def arch_shape(model: ModelInfo) -> tuple[ArchShape | None, Confidence]:
    """The KV-cache shape for a model, and how confident we are in it."""
    if model.shape is not None:
        return model.shape, Confidence.exact

    params_b = parse_params_b(model.params) or 0.0
    for candidate in _arch_candidates(model.arch):
        entry = ARCH_TABLE.get(candidate)
        if entry is None:
            continue
        buckets = entry["buckets"]
        nearest_key = min(buckets, key=lambda bucket: abs(bucket - params_b))
        return ArchShape(**buckets[nearest_key]), Confidence.good

    return None, Confidence.rough
