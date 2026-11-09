"""Unit tests for `harness_lab_app.local_tuning.memory_estimate` (pure estimate)."""

from __future__ import annotations

import json
from pathlib import Path

from harness_lab_app.local_tuning.contracts import (
    Confidence,
    LoadConfig,
    MachineSpec,
    ModelInfo,
    Verdict,
)
from harness_lab_app.local_tuning.memory_estimate import estimate_memory

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "local_tuning"


def _load_bonsai_record() -> dict:
    data = json.loads((FIXTURES_DIR / "lmstudio_v1_models.json").read_text())
    for record in data["models"]:
        if record["key"] == "prism-ml/bonsai-27b":
            return record
    raise AssertionError("bonsai-27b fixture record not found")


def test_bonsai_27b_acceptance_matches_observed_gpu_memory() -> None:
    record = _load_bonsai_record()
    model = ModelInfo(
        id=record["key"],
        display_name=record["display_name"],
        arch=record["architecture"],
        params=record["params_string"],
        quantization=record["quantization"]["name"],
        bits_per_weight=record["quantization"]["bits_per_weight"],
        size_bytes=record["size_bytes"],
        max_context=record["max_context_length"],
    )
    config = LoadConfig(
        context_length=50432,
        flash_attention=True,
        offload_kv_cache_to_gpu=True,
        kv_cache_bytes=2.0,  # f16 KV cache
    )
    machine = MachineSpec(
        memory_kind="discrete", gpu_gb=24.0, ram_gb=64.0, reserve_gb=1.0
    )

    estimate = estimate_memory(model, config, machine)

    observed_gb = 8.9
    assert observed_gb * 0.8 <= estimate.breakdown.total_gb <= observed_gb * 1.2


def test_dense_llama_8b_kv_grows_sharply_with_context_and_fails_on_small_gpu() -> None:
    model = ModelInfo(id="llama-8b", arch="llama", params="8B", size_bytes=int(8.5e9))
    machine = MachineSpec(
        memory_kind="discrete", gpu_gb=12.0, ram_gb=32.0, reserve_gb=1.0
    )

    small_config = LoadConfig(context_length=8192, flash_attention=True)
    large_config = LoadConfig(context_length=131072, flash_attention=True)

    small_estimate = estimate_memory(model, small_config, machine)
    large_estimate = estimate_memory(model, large_config, machine)

    growth = large_estimate.breakdown.kv_cache_gb / small_estimate.breakdown.kv_cache_gb
    assert growth > 14  # context grows 16x; KV cache follows closely

    assert large_estimate.verdict == Verdict.fail
    assert "context" in large_estimate.lever.lower()


def test_moe_qwen3_30b_a3b_is_detected_as_moe() -> None:
    from harness_lab_app.local_tuning.catalog import is_moe

    assert is_moe("30B-A3B") is True
    model = ModelInfo(id="qwen3-30b-a3b", arch="qwen3", params="30B-A3B", is_moe=True)
    assert model.is_moe is True


def test_unified_mac_36gb_wired_cap_makes_large_model_fail_or_tight() -> None:
    model = ModelInfo(id="big-model", arch="llama", params="22B", size_bytes=int(22e9))
    config = LoadConfig(context_length=32768, flash_attention=True)
    machine = MachineSpec(
        memory_kind="unified", gpu_gb=36.0, ram_gb=36.0, reserve_gb=4.0
    )

    estimate = estimate_memory(model, config, machine)

    assert estimate.breakdown.available_gb == 36.0 * 0.75 - 4.0
    assert estimate.verdict in (Verdict.fail, Verdict.tight)
    assert any("wired" in note.lower() for note in estimate.notes)


def test_unknown_architecture_is_rough_with_total_inside_range() -> None:
    model = ModelInfo(id="mystery", arch="mamba-xyz", params="7B", size_bytes=int(5e9))
    config = LoadConfig(context_length=32768)
    machine = MachineSpec(
        memory_kind="discrete", gpu_gb=24.0, ram_gb=64.0, reserve_gb=1.0
    )

    estimate = estimate_memory(model, config, machine)

    assert estimate.confidence == Confidence.rough
    assert estimate.breakdown.low_gb is not None
    assert estimate.breakdown.high_gb is not None
    assert (
        estimate.breakdown.low_gb
        < estimate.breakdown.total_gb
        < estimate.breakdown.high_gb
    )


def test_partial_offload_on_discrete_spills_weights_to_ram() -> None:
    model = ModelInfo(id="llama-8b", arch="llama", params="8B", size_bytes=int(16e9))
    config = LoadConfig(
        context_length=8192, gpu_offload=0.5, offload_kv_cache_to_gpu=True
    )
    machine = MachineSpec(
        memory_kind="discrete", gpu_gb=12.0, ram_gb=64.0, reserve_gb=1.0
    )

    estimate = estimate_memory(model, config, machine)

    assert estimate.breakdown.ram_gb > 0
    assert estimate.breakdown.ram_gb == model.size_bytes / 1e9 * 0.5


def test_arch_shape_exact_vs_good_vs_rough() -> None:
    from harness_lab_app.local_tuning.catalog import arch_shape
    from harness_lab_app.local_tuning.contracts import ArchShape

    shape = ArchShape(layers=10, kv_layers=10, kv_heads=2, head_dim=64)
    exact_model = ModelInfo(id="m", arch="llama", params="8B", shape=shape)
    found, confidence = arch_shape(exact_model)
    assert confidence == Confidence.exact
    assert found is shape

    good_model = ModelInfo(id="m", arch="qwen35", params="27B")
    _, good_confidence = arch_shape(good_model)
    assert good_confidence == Confidence.good

    rough_model = ModelInfo(id="m", arch="mamba-xyz", params="7B")
    _, rough_confidence = arch_shape(rough_model)
    assert rough_confidence == Confidence.rough


def test_qwen35_moe_is_catalogued_not_guessed():
    """LM Studio reports Qwen3.6 35B-A3B as `qwen35moe` (seen live, 2026-11-08)."""
    from harness_lab_app.local_tuning.catalog import arch_shape
    from harness_lab_app.local_tuning.contracts import Confidence, ModelInfo

    model = ModelInfo(id="qwen/qwen3.6-35b-a3b", arch="qwen35moe", params="35B-A3B")
    shape, confidence = arch_shape(model)
    assert confidence == Confidence.good
    assert shape is not None and shape.kv_layers < shape.layers


def test_a_rough_moe_estimate_scales_with_active_parameters():
    """Attention size follows the active parameters; 35B-A3B must not read as a dense 35B."""
    from harness_lab_app.local_tuning.contracts import (
        LoadConfig,
        MachineSpec,
        ModelInfo,
    )
    from harness_lab_app.local_tuning.memory_estimate import estimate_memory

    machine = MachineSpec(memory_kind="discrete", gpu_gb=24, ram_gb=64, reserve_gb=1)
    config = LoadConfig(context_length=50432, flash_attention=True)
    moe = ModelInfo(
        id="x", arch="unknownmoe", params="35B-A3B", size_bytes=22_000_000_000
    )
    dense = ModelInfo(id="y", arch="unknown", params="35B", size_bytes=22_000_000_000)
    moe_kv = estimate_memory(moe, config, machine).breakdown.kv_cache_gb
    dense_kv = estimate_memory(dense, config, machine).breakdown.kv_cache_gb
    assert moe_kv < dense_kv / 2


def test_a_rough_dense_estimate_stays_in_a_plausible_range():
    """A dense 27B at 50k context keeps roughly 5-15 GB of f16 KV cache."""
    from harness_lab_app.local_tuning.contracts import (
        LoadConfig,
        MachineSpec,
        ModelInfo,
    )
    from harness_lab_app.local_tuning.memory_estimate import estimate_memory

    machine = MachineSpec(memory_kind="discrete", gpu_gb=24, ram_gb=64, reserve_gb=1)
    model = ModelInfo(id="z", arch="unknown", params="27B", size_bytes=17_000_000_000)
    kv = estimate_memory(
        model, LoadConfig(context_length=50432), machine
    ).breakdown.kv_cache_gb
    assert 5 <= kv <= 15


def test_parallel_slots_share_the_context_instead_of_multiplying_it():
    """llama.cpp splits n_ctx across slots; LM Studio 0.4 served parallel=4 at 8.9 GB."""
    from harness_lab_app.local_tuning.contracts import (
        LoadConfig,
        MachineSpec,
        ModelInfo,
    )
    from harness_lab_app.local_tuning.memory_estimate import estimate_memory

    machine = MachineSpec(memory_kind="discrete", gpu_gb=24, ram_gb=64, reserve_gb=1)
    model = ModelInfo(id="b", arch="qwen35", params="27B", size_bytes=4_734_614_604)
    one = estimate_memory(model, LoadConfig(context_length=50432, parallel=1), machine)
    four = estimate_memory(model, LoadConfig(context_length=50432, parallel=4), machine)
    assert four.breakdown.kv_cache_gb == one.breakdown.kv_cache_gb
