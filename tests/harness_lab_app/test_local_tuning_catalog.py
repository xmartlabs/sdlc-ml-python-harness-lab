"""Unit tests for `harness_lab_app.local_tuning.catalog` (data-only, pure)."""

from __future__ import annotations

from harness_lab_app.local_tuning.catalog import (
    ARCH_TABLE,
    CONTEXT_OPTIONS,
    EVAL_BATCH_OPTIONS,
    KV_CACHE_TYPES,
    REFERENCE_MACHINES,
    arch_shape,
    is_moe,
    parse_params_b,
)
from harness_lab_app.local_tuning.contracts import ArchShape, Confidence, ModelInfo


def test_reference_machines_cover_unified_and_discrete() -> None:
    kinds = {machine["memory_kind"] for machine in REFERENCE_MACHINES}
    assert kinds == {"unified", "discrete"}
    assert len(REFERENCE_MACHINES) >= 12


def test_unified_machines_have_equal_ram_and_gpu_and_reserve_four() -> None:
    for machine in REFERENCE_MACHINES:
        if machine["memory_kind"] == "unified" and machine["id"] != "other":
            assert machine["ram_gb"] == machine["gpu_gb"]
            # macOS keeps ~4 GB for itself; a headless Jetson needs far less.
            expected = 1.5 if machine["id"].startswith("jetson") else 4.0
            # Every unified machine names its bandwidth: it is the speed ceiling.
            assert machine.get("bandwidth_gbs", 0) > 0, machine["id"]
            assert machine["reserve_gb"] == expected


def test_discrete_machines_reserve_one() -> None:
    for machine in REFERENCE_MACHINES:
        if machine["memory_kind"] == "discrete" and machine["id"] != "other":
            assert machine["reserve_gb"] == 1.0


def test_option_lists_match_spec() -> None:
    assert CONTEXT_OPTIONS == [8192, 16384, 32768, 50432, 65536, 131072]
    assert EVAL_BATCH_OPTIONS == [256, 512, 1024, 2048]
    assert KV_CACHE_TYPES["f16"] == 2.0
    assert KV_CACHE_TYPES["q8_0"] == 1.0625
    assert KV_CACHE_TYPES["q4_0"] == 0.5625


def test_parse_params_b_simple() -> None:
    assert parse_params_b("27B") == 27.0
    assert parse_params_b("8B") == 8.0


def test_parse_params_b_moe_notation() -> None:
    assert parse_params_b("30B-A3B") == 30.0
    assert parse_params_b("35B-A3B") == 35.0


def test_parse_params_b_expert_mix_notation() -> None:
    assert parse_params_b("8x7B") == 56.0


def test_parse_params_b_unknown_returns_none() -> None:
    assert parse_params_b("") is None
    assert parse_params_b("unknown") is None


def test_is_moe() -> None:
    assert is_moe("30B-A3B") is True
    assert is_moe("8x7B") is True
    assert is_moe("27B") is False


def test_arch_table_has_required_architectures() -> None:
    for key in ("llama", "qwen3", "qwen35", "gemma3", "gpt-oss", "mistral"):
        assert key in ARCH_TABLE


def test_arch_shape_exact_when_shape_given() -> None:
    shape = ArchShape(layers=10, kv_layers=10, kv_heads=2, head_dim=64)
    model = ModelInfo(id="m", arch="llama", params="8B", shape=shape)
    found_shape, confidence = arch_shape(model)
    assert found_shape is shape
    assert confidence == Confidence.exact


def test_arch_shape_good_for_qwen35_27b() -> None:
    model = ModelInfo(id="prism-ml/bonsai-27b", arch="qwen35", params="27B")
    shape, confidence = arch_shape(model)
    assert confidence == Confidence.good
    assert shape is not None
    assert shape.layers == 64
    assert shape.kv_layers == 16
    assert shape.kv_heads == 4
    assert shape.head_dim == 256


def test_arch_shape_rough_for_unknown_architecture() -> None:
    model = ModelInfo(id="m", arch="mamba-xyz", params="7B")
    shape, confidence = arch_shape(model)
    assert shape is None
    assert confidence == Confidence.rough


def test_arch_shape_picks_nearest_bucket_for_dense_llama() -> None:
    model = ModelInfo(id="m", arch="llama", params="70B")
    shape, confidence = arch_shape(model)
    assert confidence == Confidence.good
    assert shape is not None
    assert shape.layers == 80
    assert shape.kv_heads == 8
