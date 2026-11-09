"""Published single-user speeds: devices x models (data only).

Three tables, so the UI can filter by model, by device, or both:

- ``DEVICES``: hardware with its memory bandwidth, the ceiling on generation.
- ``MODELS``: the models with a size where one is known, so a ceiling can be
  computed for any device even when nothing is published for the pair.
- ``ROWS``: one published measurement each, with runtime, note and source.

Only numbers read on the cited page are kept, never estimates. ``secondary``
marks figures read from a summary table or a third-party aggregator rather than
a raw llama-bench row. Collected 2026-11-09.

``tg`` = token generation (write), ``pp`` = prompt processing (read), tok/s,
batch 1. Rows differ in context depth, test length (tg32/tg128, pp512/pp2048)
and build; ``note`` says how. Compare rows of the same runtime family.
"""

from __future__ import annotations

from typing import Any

# --- sources ---------------------------------------------------------------
_CUDA = "https://github.com/ggml-org/llama.cpp/discussions/15013"
_VULKAN = "https://github.com/ggml-org/llama.cpp/discussions/10879"
_ROCM = "https://github.com/ggml-org/llama.cpp/discussions/15021"
_METAL = "https://github.com/ggml-org/llama.cpp/discussions/4167"
_GPT_OSS = "https://github.com/ggml-org/llama.cpp/discussions/15396"
_SPARK_BENCH = (
    "https://github.com/ggml-org/llama.cpp/blob/master/benches/dgx-spark/dgx-spark.md"
)
_SPARK_QWEN = "https://github.com/DandinPower/llama.cpp_bench"
_GEERLING_SPARK = "https://github.com/geerlingguy/ai-benchmarks/issues/34"
_GEERLING_M3U = "https://github.com/geerlingguy/ai-benchmarks/issues/42"
_OLLAMA_SPARK = "https://ollama.com/blog/nvidia-spark-performance"
_KYUZ0 = "https://github.com/kyuz0/amd-strix-halo-toolboxes"
_FRAMEWORK = "https://community.frame.work/t/72521"
_HWC_3090 = "https://www.hardware-corner.net/gpu-llm-benchmarks/rtx-3090/"
_HWC_4090 = "https://www.hardware-corner.net/gpu-llm-benchmarks/rtx-4090/"
_HWC_5090 = "https://www.hardware-corner.net/rtx-5090-llm-benchmarks/"
_HWC_RANKING = "https://www.hardware-corner.net/gpu-ranking-local-llm/"
_LOCALSCORE = "https://www.localscore.ai/model/1"
_ZENN = "https://zenn.dev/robustonian/scraps/445f2143217f75"
_TURINGPI = "https://turingpi.com/jetson-orin-nano-8gb-llm-benchmark/"
_NVIDIA_SUPER = (
    "https://developer.nvidia.com/blog/"
    "nvidia-jetson-orin-nano-developer-kit-gets-a-super-boost/"
)
_MINI_M4 = (
    "https://vettedconsumer.com/mac-mini-m4-32gb-first-party-benchmark-qwen3-llama-cpp/"
)
_BONSAI_CARD = "https://huggingface.co/prism-ml/Bonsai-27B-gguf"
_BONSAI_SPARK = "https://blog.kubesimplify.com/bonsai-27b-rtx-pro-6000-dgx-spark"
_BONSAI_CPU = (
    "https://bigiron.cc/guides/"
    "bonsai-27b-1-bit-on-a-cpu-does-a-27b-in-4gb-beat-a-small-model"
)
_LLM_BENCH_IO = "https://llm-bench.io/models/bonsai-27b"

# --- devices ----------------------------------------------------------------
# bandwidth_gbs: vendor specification (Apple support/spec pages, NVIDIA, AMD).
# kind: "gpu" (discrete VRAM) or "unified" (one pool shared with the CPU).
DEVICES: list[dict[str, Any]] = [
    {
        "id": "rtx3060",
        "label": "RTX 3060 12 GB",
        "kind": "gpu",
        "bandwidth_gbs": 360,
        "memory": "12 GB GDDR6",
    },
    {
        "id": "rtx4060ti",
        "label": "RTX 4060 Ti 8 GB",
        "kind": "gpu",
        "bandwidth_gbs": 288,
        "memory": "8 GB GDDR6",
    },
    {
        "id": "rtx3080",
        "label": "RTX 3080 10 GB",
        "kind": "gpu",
        "bandwidth_gbs": 760,
        "memory": "10 GB GDDR6X",
    },
    {
        "id": "rtx3090",
        "label": "RTX 3090",
        "kind": "gpu",
        "bandwidth_gbs": 936,
        "memory": "24 GB GDDR6X",
    },
    {
        "id": "rtx3090ti",
        "label": "RTX 3090 Ti",
        "kind": "gpu",
        "bandwidth_gbs": 1008,
        "memory": "24 GB GDDR6X",
    },
    {
        "id": "rtx4080",
        "label": "RTX 4080",
        "kind": "gpu",
        "bandwidth_gbs": 717,
        "memory": "16 GB GDDR6X",
    },
    {
        "id": "rtx4090",
        "label": "RTX 4090",
        "kind": "gpu",
        "bandwidth_gbs": 1008,
        "memory": "24 GB GDDR6X",
    },
    {
        "id": "rtx5080",
        "label": "RTX 5080",
        "kind": "gpu",
        "bandwidth_gbs": 960,
        "memory": "16 GB GDDR7",
    },
    {
        "id": "rtx5090",
        "label": "RTX 5090",
        "kind": "gpu",
        "bandwidth_gbs": 1792,
        "memory": "32 GB GDDR7",
    },
    {
        "id": "rtxpro6000",
        "label": "RTX PRO 6000 (Blackwell)",
        "kind": "gpu",
        "bandwidth_gbs": 1792,
        "memory": "96 GB GDDR7",
    },
    {
        "id": "h100",
        "label": "H100",
        "kind": "gpu",
        "bandwidth_gbs": None,
        "memory": "80 GB HBM (PCIe and SXM differ)",
    },
    {
        "id": "rx7900gre",
        "label": "Radeon RX 7900 GRE",
        "kind": "gpu",
        "bandwidth_gbs": 576,
        "memory": "16 GB GDDR6",
    },
    {
        "id": "dgx-spark",
        "label": "NVIDIA DGX Spark (GB10)",
        "kind": "unified",
        "bandwidth_gbs": 273,
        "memory": "128 GB LPDDR5x unified",
    },
    {
        "id": "ryzen-ai-max-395",
        "label": "AMD Ryzen AI Max+ 395 (Strix Halo)",
        "kind": "unified",
        "bandwidth_gbs": 256,
        "memory": "up to 128 GB LPDDR5x (GPU: 96 GB Windows, ~120 GB Linux)",
    },
    {
        "id": "jetson-orin-nano-super",
        "label": "Jetson Orin Nano Super 8 GB",
        "kind": "unified",
        "bandwidth_gbs": 102,
        "memory": "8 GB LPDDR5 unified (MAXN)",
    },
    {
        "id": "jetson-orin-nano",
        "label": "Jetson Orin Nano 8 GB (original clocks)",
        "kind": "unified",
        "bandwidth_gbs": 68,
        "memory": "8 GB LPDDR5 unified",
    },
    {
        "id": "jetson-agx-orin-64",
        "label": "Jetson AGX Orin 64 GB",
        "kind": "unified",
        "bandwidth_gbs": 204.8,
        "memory": "64 GB LPDDR5 unified",
    },
    {
        "id": "mac-mini-m4",
        "label": "Mac mini M4 (10-core GPU)",
        "kind": "unified",
        "bandwidth_gbs": 120,
        "memory": "16 / 24 / 32 GB",
    },
    {
        "id": "mac-mini-m4-pro",
        "label": "Mac mini M4 Pro",
        "kind": "unified",
        "bandwidth_gbs": 273,
        "memory": "24 / 48 / 64 GB",
    },
    {
        "id": "mac-mini-m5-pro",
        "label": "Mac mini M5 Pro",
        "kind": "unified",
        "bandwidth_gbs": 307,
        "memory": "24 / 48 / 64 GB",
    },
    {
        "id": "mac-mini-m6",
        "label": "Mac mini M6",
        "kind": "unified",
        "bandwidth_gbs": 170,
        "memory": "16 GB (153 GB/s) / 24 / 32 GB",
    },
    {
        "id": "m4-max-410",
        "label": "M4 Max 32-core GPU (Mac Studio / MacBook Pro)",
        "kind": "unified",
        "bandwidth_gbs": 410,
        "memory": "36 GB",
    },
    {
        "id": "m4-max-546",
        "label": "M4 Max 40-core GPU (Mac Studio / MacBook Pro)",
        "kind": "unified",
        "bandwidth_gbs": 546,
        "memory": "48 / 64 / 128 GB",
    },
    {
        "id": "m3-ultra",
        "label": "Mac Studio M3 Ultra",
        "kind": "unified",
        "bandwidth_gbs": 819,
        "memory": "96 GB to 512 GB",
    },
    {
        "id": "m5-pro",
        "label": "M5 Pro (20-core GPU)",
        "kind": "unified",
        "bandwidth_gbs": 307,
        "memory": "24 / 48 / 64 GB",
    },
    {
        "id": "m5-max-614",
        "label": "M5 Max 40-core GPU (Mac Studio / MacBook Pro)",
        "kind": "unified",
        "bandwidth_gbs": 614,
        "memory": "48 / 64 / 128 GB",
    },
    {
        "id": "m5-ultra",
        "label": "Mac Studio M5 Ultra",
        "kind": "unified",
        "bandwidth_gbs": 1228,
        "memory": "96 GB to 512 GB",
    },
    {
        "id": "m1-max-32",
        "label": "M1 Max 32-core GPU",
        "kind": "unified",
        "bandwidth_gbs": 400,
        "memory": "32 / 64 GB",
    },
    {
        "id": "m2-max-38",
        "label": "M2 Max 38-core GPU",
        "kind": "unified",
        "bandwidth_gbs": 400,
        "memory": "32 / 64 / 96 GB",
    },
    {
        "id": "m2-ultra-76",
        "label": "M2 Ultra 76-core GPU",
        "kind": "unified",
        "bandwidth_gbs": 800,
        "memory": "64 / 128 / 192 GB",
    },
    {
        "id": "m3-pro-18",
        "label": "M3 Pro 18-core GPU",
        "kind": "unified",
        "bandwidth_gbs": 150,
        "memory": "18 / 36 GB",
    },
    {
        "id": "m3-max-40",
        "label": "M3 Max 40-core GPU",
        "kind": "unified",
        "bandwidth_gbs": 400,
        "memory": "48 / 64 / 128 GB",
    },
    {
        "id": "cpu-only",
        "label": "CPU only (no GPU)",
        "kind": "cpu",
        "bandwidth_gbs": None,
        "memory": "system RAM",
    },
]

# --- models -------------------------------------------------------------------
# size_gb: file size as a server reported it (LM Studio / Ollama, 2026-11-09)
# or as llama-bench printed it; None when no measured size was at hand.
# params uses the "total-Aactive" form for MoE so the ceiling uses active weights.
MODELS: list[dict[str, Any]] = [
    {
        "id": "bonsai-27b-q1",
        "label": "Bonsai 27B · Q1_0 (1-bit, Qwen3.6-27B)",
        "params": "27B",
        "quant": "Q1_0",
        "size_gb": 4.73,
        "match": ["bonsai-27b"],
    },
    {
        "id": "qwen3-4b-q4",
        "label": "Qwen3 4B · Q4_K_M",
        "params": "4.0B",
        "quant": "Q4_K_M",
        "size_gb": 2.50,
        "match": ["qwen3-4b", "qwen3:4b"],
    },
    {
        "id": "qwen3-8b-q4",
        "label": "Qwen3 8B · Q4_K_M",
        "params": "8.2B",
        "quant": "Q4_K_M",
        "size_gb": 5.23,
        "match": ["qwen3-8b", "qwen3:8b", "qwen3_8b"],
    },
    {
        "id": "qwen3-30b-a3b-q4",
        "label": "Qwen3 30B-A3B (MoE) · Q4_K_M",
        "params": "30B-A3B",
        "quant": "Q4_K_M",
        "size_gb": 18.55,
        "match": ["qwen3-30b-a3b", "qwen3:30b"],
    },
    {
        "id": "qwen3-coder-30b-a3b-q4",
        "label": "Qwen3 Coder 30B-A3B (MoE) · Q4",
        "params": "30B-A3B",
        "quant": "Q4_K_M",
        "size_gb": 18.63,
        "match": ["qwen3-coder-30b", "qwen3-coder:30b"],
    },
    {
        "id": "qwen3-32b-q4",
        "label": "Qwen3 32B (dense) · Q4_K_M",
        "params": "32B",
        "quant": "Q4_K_M",
        "size_gb": None,
        "match": ["qwen3-32b", "qwen3:32b"],
    },
    {
        "id": "gpt-oss-20b",
        "label": "gpt-oss 20B · MXFP4",
        "params": "21B-A3.6B",
        "quant": "MXFP4",
        "size_gb": 12.11,
        "match": ["gpt-oss-20b", "gpt-oss:20b"],
    },
    {
        "id": "gpt-oss-120b",
        "label": "gpt-oss 120B · MXFP4",
        "params": "117B-A5.1B",
        "quant": "MXFP4",
        "size_gb": None,
        "match": ["gpt-oss-120b", "gpt-oss:120b"],
    },
    {
        "id": "llama31-8b-q4",
        "label": "Llama 3.1 8B · Q4_K_M",
        "params": "8B",
        "quant": "Q4_K_M",
        "size_gb": None,
        "match": ["llama-3.1-8b", "llama3.1:8b", "meta-llama-3.1-8b"],
    },
    {
        "id": "llama32-3b-q4",
        "label": "Llama 3.2 3B · Q4_K_M",
        "params": "3B",
        "quant": "Q4_K_M",
        "size_gb": None,
        "match": ["llama-3.2-3b", "llama3.2:3b"],
    },
    {
        "id": "llama2-7b-q4_0",
        "label": "Llama 2 7B · Q4_0 (llama.cpp hardware scoreboard)",
        "params": "7B",
        "quant": "Q4_0",
        "size_gb": 3.82,
        "match": ["llama-2-7b"],
    },
]


def _r(model, device, tg, pp, runtime, note, source, secondary=False):
    return {
        "model": model,
        "device": device,
        "tg": tg,
        "pp": pp,
        "runtime": runtime,
        "note": note,
        "source": source,
        "secondary": secondary,
    }


ROWS: list[dict[str, Any]] = [
    # Bonsai 27B Q1_0 (1-bit; compute-bound, the bandwidth ceiling overstates it)
    _r(
        "bonsai-27b-q1",
        "m5-max-614",
        66.4,
        874,
        "Prism llama.cpp fork, Metal",
        "MacBook Pro M5 Max, tg128 / pp512",
        _BONSAI_CARD,
    ),
    _r(
        "bonsai-27b-q1",
        "m5-pro",
        44.2,
        421,
        "Prism llama.cpp fork, Metal",
        "MacBook Pro M5 Pro, tg128 / pp512",
        _BONSAI_CARD,
    ),
    _r(
        "bonsai-27b-q1",
        "mac-mini-m4-pro",
        26.0,
        133,
        "Prism llama.cpp fork, Metal",
        "M4 Pro laptop (same chip), tg128 / pp512",
        _BONSAI_CARD,
    ),
    _r(
        "bonsai-27b-q1",
        "h100",
        104.8,
        2755,
        "Prism llama.cpp fork, CUDA",
        "143.8 with its speculative drafter",
        _BONSAI_CARD,
    ),
    _r(
        "bonsai-27b-q1",
        "dgx-spark",
        42.8,
        991.6,
        "Prism llama.cpp fork, CUDA",
        "mainline llama.cpp: 40.9 / 922.9; 31.5 tg at 16k depth",
        _BONSAI_SPARK,
    ),
    _r(
        "bonsai-27b-q1",
        "rtxpro6000",
        145.5,
        3599.7,
        "Prism llama.cpp fork, CUDA",
        "mainline llama.cpp: 133.7 / 3658.6",
        _BONSAI_SPARK,
    ),
    _r(
        "bonsai-27b-q1",
        "rx7900gre",
        53.2,
        None,
        "LM Studio",
        "aggregator listing",
        _LLM_BENCH_IO,
        secondary=True,
    ),
    _r(
        "bonsai-27b-q1",
        "cpu-only",
        6.71,
        13.19,
        "Prism llama.cpp fork, CPU",
        "mainline: 6.25 / 7.91",
        _BONSAI_CPU,
    ),
    # Qwen3 4B
    # (no published llama.cpp figure found yet; your own tests appear here)
    # Qwen3 8B Q4_K_M
    _r(
        "qwen3-8b-q4",
        "rtx3060",
        41.97,
        1119.23,
        "llama-bench, Q4_K_XL",
        "at 16k context depth",
        _HWC_RANKING,
        secondary=True,
    ),
    _r(
        "qwen3-8b-q4",
        "rtx3090",
        115.3,
        4049.6,
        "llama.cpp CUDA, Q4_K, FA",
        "at 4k context depth",
        _HWC_3090,
        secondary=True,
    ),
    _r(
        "qwen3-8b-q4",
        "rtx4090",
        141.3,
        9250.5,
        "llama.cpp CUDA, Q4_K, FA",
        "at 4k depth (16k: tg 108.0)",
        _HWC_4090,
        secondary=True,
    ),
    _r(
        "qwen3-8b-q4",
        "rtx5090",
        185.91,
        10406.47,
        "llama-bench, Q4_K_XL, FA",
        "at 4k context depth",
        _HWC_5090,
        secondary=True,
    ),
    _r(
        "qwen3-8b-q4",
        "dgx-spark",
        43.89,
        3167.93,
        "llama-bench, CUDA",
        "tg64 at 512 depth / pp512, build 7280",
        _SPARK_QWEN,
    ),
    _r(
        "qwen3-8b-q4",
        "jetson-orin-nano-super",
        12.64,
        355.5,
        "llama-bench, FA, FP16 KV",
        "25 W mode (below MAXN), tg128 / pp512",
        _TURINGPI,
    ),
    _r(
        "qwen3-8b-q4",
        "mac-mini-m4",
        19.7,
        223.8,
        "llama.cpp b10929, Metal",
        "Mac mini M4 32 GB",
        _MINI_M4,
        secondary=True,
    ),
    # Qwen3 30B-A3B
    _r(
        "qwen3-30b-a3b-q4",
        "rtx3090",
        153.6,
        2988.6,
        "llama.cpp CUDA, Q4_K, FA",
        "at 4k context depth",
        _HWC_3090,
        secondary=True,
    ),
    _r(
        "qwen3-30b-a3b-q4",
        "rtx4090",
        207.3,
        6159.4,
        "llama.cpp CUDA, Q4_K, FA",
        "at 4k depth (16k: tg 139.2)",
        _HWC_4090,
        secondary=True,
    ),
    _r(
        "qwen3-30b-a3b-q4",
        "rtx5090",
        234.30,
        6630.42,
        "llama-bench, Q4_K_XL, FA",
        "at 4k context depth",
        _HWC_5090,
        secondary=True,
    ),
    _r(
        "qwen3-30b-a3b-q4",
        "dgx-spark",
        89.57,
        2541.61,
        "llama-bench, CUDA",
        "tg64 at 512 depth / pp512, build 7280",
        _SPARK_QWEN,
    ),
    _r(
        "qwen3-30b-a3b-q4",
        "ryzen-ai-max-395",
        66.3,
        604.8,
        "llama.cpp Vulkan, UD-Q4_K_XL",
        "July 2025, older build",
        _FRAMEWORK,
        secondary=True,
    ),
    # Qwen3 Coder 30B-A3B
    _r(
        "qwen3-coder-30b-a3b-q4",
        "rtx3060",
        26.70,
        412.71,
        "llama-bench, UD-Q4_K_XL, FA",
        "partial offload (30 layers): does not fit in 12 GB",
        _ZENN,
    ),
    _r(
        "qwen3-coder-30b-a3b-q4",
        "rtx3090",
        144.78,
        2350.00,
        "llama-bench, UD-Q4_K_XL, FA",
        "tg128 / pp512, build 6060",
        _ZENN,
    ),
    _r(
        "qwen3-coder-30b-a3b-q4",
        "ryzen-ai-max-395",
        86.69,
        1075.31,
        "llama.cpp Vulkan RADV, Q4_K_M",
        "ROCm 7.2: 71.51 / 1207.32",
        _KYUZ0,
    ),
    # Qwen3 32B
    _r(
        "qwen3-32b-q4",
        "rtx3090",
        35.1,
        1087.9,
        "llama.cpp CUDA, Q4_K, FA",
        "at 4k context depth",
        _HWC_3090,
        secondary=True,
    ),
    _r(
        "qwen3-32b-q4",
        "rtx4090",
        39.6,
        2448.0,
        "llama.cpp CUDA, Q4_K, FA",
        "at 4k context depth",
        _HWC_4090,
        secondary=True,
    ),
    _r(
        "qwen3-32b-q4",
        "rtx5090",
        61.38,
        2931.32,
        "llama-bench, Q4_K_XL, FA",
        "at 4k context depth",
        _HWC_5090,
        secondary=True,
    ),
    _r(
        "qwen3-32b-q4",
        "dgx-spark",
        10.43,
        762.22,
        "llama-bench, CUDA",
        "tg64 at 512 depth / pp512",
        _SPARK_QWEN,
    ),
    _r(
        "qwen3-32b-q4",
        "m3-ultra",
        26.69,
        335.28,
        "llama-bench, Metal",
        "M3 Ultra 512 GB, tg128 / pp512",
        _GEERLING_M3U,
    ),
    # gpt-oss 20B
    _r(
        "gpt-oss-20b",
        "rtx3060",
        64.0,
        None,
        "llama-server, -ncmoe 2",
        "16k context; 75 at 2k fully on GPU",
        _GPT_OSS,
    ),
    _r(
        "gpt-oss-20b",
        "rtx3090",
        161.77,
        5170.56,
        "llama-bench, FA",
        "tg128 / pp2048",
        _GPT_OSS,
    ),
    _r(
        "gpt-oss-20b",
        "rtx4090",
        221.95,
        8022.33,
        "llama-bench, FA",
        "tg128 / pp2048",
        _GPT_OSS,
    ),
    _r(
        "gpt-oss-20b",
        "rtx5090",
        282.51,
        9848.38,
        "llama-bench, FA",
        "tg128 / pp2048",
        _GPT_OSS,
    ),
    _r(
        "gpt-oss-20b",
        "dgx-spark",
        83.43,
        4505.82,
        "llama-bench, CUDA",
        "tg32 / pp2048, build 7941; at 32k depth: 61.65 / 2689.42",
        _SPARK_BENCH,
    ),
    _r(
        "gpt-oss-20b",
        "ryzen-ai-max-395",
        79.78,
        1692.49,
        "llama-bench, Vulkan RADV, FA",
        "tg128 / pp512; ROCm 7.2.3: 73.05 / 1786.17",
        _KYUZ0,
    ),
    _r(
        "gpt-oss-20b",
        "mac-mini-m4-pro",
        63.34,
        700.78,
        "llama-bench, Metal",
        "Mac mini M4 Pro 64 GB, tg128 / pp2048",
        _GPT_OSS,
    ),
    _r(
        "gpt-oss-20b",
        "m4-max-410",
        92.36,
        1277.42,
        "llama-bench, Metal",
        "M4 Max 36 GB, tg128 / pp2048",
        _GPT_OSS,
    ),
    _r(
        "gpt-oss-20b",
        "m3-ultra",
        115.52,
        2816.47,
        "llama-bench, Metal",
        "80-core GPU, tg128 / pp2048",
        _GPT_OSS,
    ),
    # gpt-oss 120B
    _r(
        "gpt-oss-120b",
        "dgx-spark",
        58.72,
        2443.91,
        "llama-bench, CUDA",
        "tg32 / pp2048, build 7941; at 32k depth: 42.76 / 1567.08",
        _SPARK_BENCH,
    ),
    _r(
        "gpt-oss-120b",
        "ryzen-ai-max-395",
        56.61,
        719.91,
        "llama-bench, Vulkan RADV, FA",
        "tg128 / pp512; ROCm 7.2.3: 51.81 / 625.58",
        _KYUZ0,
    ),
    _r(
        "gpt-oss-120b",
        "m3-ultra",
        78.41,
        1286.04,
        "llama-bench, Metal, Q4_K_M re-quant",
        "not MXFP4; tg128 / pp512",
        _GEERLING_M3U,
    ),
    _r(
        "gpt-oss-120b",
        "m2-ultra-76",
        79.68,
        1244.57,
        "llama-bench, Metal, MXFP4",
        "tg128 / pp2048",
        _GPT_OSS,
    ),
    # Llama 3.1 8B
    _r(
        "llama31-8b-q4",
        "rtx3060",
        50.1,
        1478,
        "LocalScore (llamafile)",
        "LocalScore's own harness",
        _LOCALSCORE,
        secondary=True,
    ),
    _r(
        "llama31-8b-q4",
        "rtx3090",
        96.3,
        3550,
        "LocalScore (llamafile)",
        "LocalScore's own harness",
        _LOCALSCORE,
        secondary=True,
    ),
    _r(
        "llama31-8b-q4",
        "rtx4090",
        120.0,
        7594,
        "LocalScore (llamafile)",
        "LocalScore's own harness",
        _LOCALSCORE,
        secondary=True,
    ),
    _r(
        "llama31-8b-q4",
        "m4-max-546",
        54.5,
        656,
        "LocalScore (llamafile)",
        "LocalScore's own harness",
        _LOCALSCORE,
        secondary=True,
    ),
    _r(
        "llama31-8b-q4",
        "dgx-spark",
        38.02,
        7614,
        "Ollama 0.12.6",
        "500 output tokens",
        _OLLAMA_SPARK,
        secondary=True,
    ),
    _r(
        "llama31-8b-q4",
        "jetson-orin-nano-super",
        19.14,
        None,
        "MLC INT4 (not llama.cpp)",
        "MAXN; NVIDIA's figure. Original Orin Nano: 14",
        _NVIDIA_SUPER,
    ),
    # Llama 3.2 3B
    _r(
        "llama32-3b-q4",
        "jetson-orin-nano-super",
        25.24,
        813.7,
        "llama-bench",
        "25 W mode (below MAXN), tg128 / pp512",
        _TURINGPI,
    ),
    _r(
        "llama32-3b-q4",
        "jetson-orin-nano-super",
        43.07,
        None,
        "MLC INT4 (not llama.cpp)",
        "MAXN; NVIDIA's figure",
        _NVIDIA_SUPER,
    ),
    # Llama 2 7B Q4_0: the llama.cpp hardware scoreboard (same model everywhere)
    _r(
        "llama2-7b-q4_0",
        "rtx3060",
        76.92,
        2407.67,
        "llama-bench CUDA, FA",
        "tg128 / pp512",
        _CUDA,
    ),
    _r(
        "llama2-7b-q4_0",
        "rtx4060ti",
        64.03,
        3803.45,
        "llama-bench CUDA, FA",
        "tg128 / pp512",
        _CUDA,
    ),
    _r(
        "llama2-7b-q4_0",
        "rtx3080",
        139.95,
        5569.56,
        "llama-bench CUDA, FA",
        "tg128 / pp512",
        _CUDA,
    ),
    _r(
        "llama2-7b-q4_0",
        "rtx3090",
        161.89,
        5560.06,
        "llama-bench CUDA, FA",
        "tg128 / pp512",
        _CUDA,
    ),
    _r(
        "llama2-7b-q4_0",
        "rtx3090ti",
        172.26,
        6924.01,
        "llama-bench CUDA, FA",
        "tg128 / pp512",
        _CUDA,
    ),
    _r(
        "llama2-7b-q4_0",
        "rtx4080",
        143.47,
        9205.93,
        "llama-bench CUDA, FA",
        "tg128 / pp512",
        _CUDA,
    ),
    _r(
        "llama2-7b-q4_0",
        "rtx4090",
        188.96,
        14770.63,
        "llama-bench CUDA, FA",
        "tg128 / pp512",
        _CUDA,
    ),
    _r(
        "llama2-7b-q4_0",
        "rtx5080",
        184.68,
        9487.70,
        "llama-bench CUDA, FA",
        "tg128 / pp512",
        _CUDA,
    ),
    _r(
        "llama2-7b-q4_0",
        "rtx5090",
        300.40,
        14970.15,
        "llama-bench CUDA, FA",
        "tg128 / pp512",
        _CUDA,
    ),
    _r(
        "llama2-7b-q4_0",
        "dgx-spark",
        60.39,
        3321.91,
        "llama-bench Vulkan (coopmat2), FA",
        "CUDA FA: 56.74 / 3661.37",
        _VULKAN,
    ),
    _r(
        "llama2-7b-q4_0",
        "ryzen-ai-max-395",
        54.38,
        1704.30,
        "llama-bench Vulkan, FA",
        "ROCm FA: 49.87 / 1003.53",
        _VULKAN,
    ),
    _r(
        "llama2-7b-q4_0",
        "jetson-agx-orin-64",
        33.58,
        991.31,
        "llama-bench CUDA",
        "tg128 / pp512",
        _CUDA,
    ),
    _r(
        "llama2-7b-q4_0",
        "mac-mini-m4",
        24.11,
        221.29,
        "llama-bench Metal",
        "2023 build; current builds are faster",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "mac-mini-m4-pro",
        50.74,
        439.78,
        "llama-bench Metal",
        "20-core GPU, 2023 build",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m4-max-410",
        69.95,
        713.93,
        "llama-bench Metal",
        "2023 build",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m4-max-546",
        83.06,
        885.68,
        "llama-bench Metal",
        "2023 build",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m3-ultra",
        92.14,
        1471.24,
        "llama-bench Metal",
        "80-core GPU, 2023 build",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m5-pro",
        66.33,
        1620.64,
        "llama-bench Metal, FA",
        "2026 build (c1d0e7a)",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m5-max-614",
        119.92,
        3219.99,
        "llama-bench Metal, FA",
        "2026 build (c1d0e7a)",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m5-ultra",
        179.10,
        4944.66,
        "llama-bench Metal, FA",
        "2026 build (c1d0e7a)",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m1-max-32",
        61.19,
        530.06,
        "llama-bench Metal",
        "2023 build",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m2-max-38",
        65.95,
        671.31,
        "llama-bench Metal",
        "2023 build",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m2-ultra-76",
        125.21,
        1488.81,
        "llama-bench Metal, FA",
        "2026 build (2023 build: 94.27 / 1238.48)",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m3-pro-18",
        30.74,
        341.67,
        "llama-bench Metal",
        "2023 build",
        _METAL,
    ),
    _r(
        "llama2-7b-q4_0",
        "m3-max-40",
        66.31,
        759.70,
        "llama-bench Metal",
        "2023 build",
        _METAL,
    ),
]

_DEVICE_BY_ID = {d["id"]: d for d in DEVICES}
_MODEL_BY_ID = {m["id"]: m for m in MODELS}


def ceiling_tps(model: dict[str, Any], device: dict[str, Any]) -> float | None:
    """Bandwidth ceiling on generation for this model on this device."""
    from harness_lab_app.local_tuning.headroom import bytes_per_token_gb

    if not model.get("size_gb") or not device.get("bandwidth_gbs"):
        return None
    per_token = bytes_per_token_gb(int(model["size_gb"] * 1e9), model["params"])
    if not per_token:
        return None
    return round(device["bandwidth_gbs"] / per_token, 1)


def reference_payload() -> dict[str, Any]:
    """Devices, models and rows, each row with its ceiling and share of it."""
    rows = []
    for row in ROWS:
        model, device = _MODEL_BY_ID[row["model"]], _DEVICE_BY_ID[row["device"]]
        ceiling = ceiling_tps(model, device)
        rows.append(
            {
                **row,
                "ceiling_tps": ceiling,
                "fraction": round(row["tg"] / ceiling, 3) if ceiling else None,
            }
        )
    return {
        "devices": DEVICES,
        "models": [{k: v for k, v in m.items() if k != "match"} for m in MODELS],
        "rows": rows,
    }


def match_reference(model_id: str) -> str | None:
    """The reference model a served id corresponds to, if any (by name)."""
    lowered = model_id.lower()
    for model in MODELS:
        if any(token in lowered for token in model["match"]):
            return str(model["id"])
    return None
