"""How far a measured write speed is from what the hardware allows (pure).

Token generation reads every active weight once per token, so it is bound by
memory bandwidth: ``ceiling tok/s ≈ bandwidth / bytes read per token``. Real
setups reach a fraction of that ceiling, and the fraction answers the user's
question "is it worth tuning further?":

- Published llama.cpp results (Llama 2 7B Q4_0, 3.82 GB) reach 64-81 % of the
  ceiling on consumer NVIDIA GPUs, e.g. RTX 3090 162 of 245 tok/s (66 %),
  RTX 4090 189 of 264 (72 %), RTX 3060 77 of 94 (81 %).
  Source: ggml-org/llama.cpp discussion #15013 (retrieved 2026-11-09).
- Apple silicon behaves the same way (discussion #4167: tg scales with
  bandwidth across M1-M5).

Prompt reading (prefill) is compute-bound, so it has no bandwidth ceiling and
is compared against the published pp512 figure instead, which is only a rough
guide because it depends on model size.
"""

from __future__ import annotations

from typing import Any

from harness_lab_app.local_tuning.catalog import active_params_b, parse_params_b

# Fraction of the bandwidth ceiling a well-configured setup reaches (see above).
NEAR_CEILING = 0.60
SOME_HEADROOM = 0.35

# Below ~2 bits per weight, dequantisation compute (not bandwidth) limits
# generation, so the ceiling overstates what is reachable.
_LOW_BIT_QUANTS = ("Q1", "IQ1", "Q2", "IQ2", "TQ1", "TQ2")

# Published reference: llama-bench, Llama 2 7B Q4_0, flash attention on.
# Bandwidths are vendor specifications (NVIDIA) and the figures cited in
# ggml-org/llama.cpp discussion #4167 (Apple). tg128/pp512 from discussions
# #15013 (CUDA scoreboard) and #4167 (Apple, build 8e672ef), retrieved 2026-11-09.
REFERENCE_DEVICES: list[dict[str, Any]] = [
    {"device": "RTX 3060 12 GB", "bandwidth_gbs": 360, "pp512": 2408, "tg128": 76.9},
    {"device": "RTX 4060 Ti 8 GB", "bandwidth_gbs": 288, "pp512": 3803, "tg128": 64.0},
    {"device": "RTX 3080 10 GB", "bandwidth_gbs": 760, "pp512": 5570, "tg128": 140.0},
    {"device": "RTX 3090", "bandwidth_gbs": 936, "pp512": 5560, "tg128": 161.9},
    {"device": "RTX 3090 Ti", "bandwidth_gbs": 1008, "pp512": 6924, "tg128": 172.3},
    {"device": "RTX 4080", "bandwidth_gbs": 717, "pp512": 9206, "tg128": 143.5},
    {"device": "RTX 4090", "bandwidth_gbs": 1008, "pp512": 14771, "tg128": 189.0},
    {"device": "RTX 5080", "bandwidth_gbs": 960, "pp512": 9488, "tg128": 184.7},
    {"device": "RTX 5090", "bandwidth_gbs": 1792, "pp512": 14970, "tg128": 300.4},
    # The Orin *Nano* has no Llama 2 7B scoreboard row; the AGX (204.8 GB/s,
    # NVIDIA spec) is the nearest Jetson on the same benchmark.
    {
        "device": "Jetson AGX Orin 64 GB",
        "bandwidth_gbs": 204.8,
        "pp512": 991,
        "tg128": 33.6,
    },
    {
        "device": "M1 Pro (16-core GPU)",
        "bandwidth_gbs": 200,
        "pp512": 266,
        "tg128": 36.4,
    },
    {
        "device": "M1 Max (32-core GPU)",
        "bandwidth_gbs": 400,
        "pp512": 530,
        "tg128": 61.2,
    },
    {
        "device": "M2 Max (38-core GPU)",
        "bandwidth_gbs": 400,
        "pp512": 671,
        "tg128": 66.0,
    },
    {
        "device": "M2 Ultra (76-core GPU)",
        "bandwidth_gbs": 800,
        "pp512": 1238,
        "tg128": 94.3,
    },
    {
        "device": "M3 Pro (18-core GPU)",
        "bandwidth_gbs": 150,
        "pp512": 342,
        "tg128": 30.7,
    },
    {
        "device": "M3 Max (40-core GPU)",
        "bandwidth_gbs": 400,
        "pp512": 760,
        "tg128": 66.3,
    },
    {
        "device": "M4 Pro (20-core GPU)",
        "bandwidth_gbs": 273,
        "pp512": 440,
        "tg128": 50.7,
    },
    {
        "device": "M4 Max (40-core GPU)",
        "bandwidth_gbs": 546,
        "pp512": 886,
        "tg128": 83.1,
    },
]
REFERENCE_MODEL = "Llama 2 7B Q4_0 (3.82 GB), llama-bench pp512 / tg128"
REFERENCE_SOURCES = [
    "https://github.com/ggml-org/llama.cpp/discussions/15013",
    "https://github.com/ggml-org/llama.cpp/discussions/4167",
]


def bytes_per_token_gb(size_bytes: int | None, params: str) -> float | None:
    """Gigabytes of weights read to generate one token.

    A dense model reads all of them. A mixture-of-experts model reads its
    active share, approximated by active/total parameters.
    """
    if not size_bytes:
        return None
    size_gb = size_bytes / 1e9
    total = parse_params_b(params)
    active = active_params_b(params)
    if total and active and active < total:
        return size_gb * active / total
    return size_gb


def is_low_bit(quantization: str) -> bool:
    return quantization.strip().upper().startswith(_LOW_BIT_QUANTS)


def write_headroom(
    measured_tps: float | None,
    bandwidth_gbs: float | None,
    size_bytes: int | None,
    params: str,
    quantization: str = "",
) -> dict[str, Any] | None:
    """Measured write speed against the bandwidth ceiling, with a plain reading.

    ``None`` when the machine's bandwidth or the model size is unknown.
    """
    per_token = bytes_per_token_gb(size_bytes, params)
    if not measured_tps or not bandwidth_gbs or not per_token:
        return None
    ceiling = bandwidth_gbs / per_token
    fraction = measured_tps / ceiling
    typical_low, typical_high = ceiling * NEAR_CEILING, ceiling * 0.8
    if is_low_bit(quantization):
        level = "compute_bound"
        reading = (
            f"Writes {measured_tps:.0f} tok/s, {fraction:.0%} of the "
            f"{ceiling:.0f} tok/s bandwidth ceiling. {quantization} is a 1-2 bit "
            "quantization: unpacking it costs compute, so generation stops being "
            "bandwidth-bound and this ceiling overstates what is reachable. A "
            "4-bit quantization of the same model is the fairer comparison."
        )
    elif fraction >= NEAR_CEILING:
        level = "near_ceiling"
        reading = (
            f"Writes {measured_tps:.0f} tok/s, {fraction:.0%} of the {ceiling:.0f} "
            "tok/s this GPU's memory bandwidth allows. Well-tuned setups reach "
            f"60-80 %, so there is little left to gain from settings; a smaller "
            "quantization or a faster GPU is what moves it now."
        )
    elif fraction >= SOME_HEADROOM:
        level = "some_headroom"
        reading = (
            f"Writes {measured_tps:.0f} tok/s, {fraction:.0%} of the {ceiling:.0f} "
            f"tok/s ceiling; well-tuned setups reach ~{typical_low:.0f}-"
            f"{typical_high:.0f}. Worth checking: all layers on the GPU, flash "
            "attention on, nothing else using the GPU."
        )
    else:
        level = "far_below"
        reading = (
            f"Writes {measured_tps:.0f} tok/s, only {fraction:.0%} of the "
            f"{ceiling:.0f} tok/s ceiling (~{typical_low:.0f}-{typical_high:.0f} "
            "expected). Something is likely off: layers on the CPU (GPU offload "
            "below 100 %), the model spilling into shared memory, or another "
            "model sharing the GPU."
        )
    return {
        "level": level,
        "ceiling_tps": round(ceiling, 1),
        "fraction": round(fraction, 3),
        "typical_tps": [round(typical_low, 1), round(typical_high, 1)],
        "bytes_per_token_gb": round(per_token, 2),
        "reading": reading,
    }
