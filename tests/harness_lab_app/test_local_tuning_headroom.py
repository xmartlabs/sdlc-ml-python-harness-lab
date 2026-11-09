"""Write speed against the memory-bandwidth ceiling."""

from __future__ import annotations

import pytest

from harness_lab_app.local_tuning.headroom import (
    REFERENCE_DEVICES,
    bytes_per_token_gb,
    write_headroom,
)


def test_published_results_sit_in_the_band_the_verdict_calls_near_ceiling():
    """The reference rows themselves must read as well-tuned (Llama 2 7B Q4_0)."""
    size = int(3.82e9)
    for row in REFERENCE_DEVICES:
        if not row["device"].startswith("RTX"):
            continue
        result = write_headroom(row["tg128"], row["bandwidth_gbs"], size, "7B", "Q4_0")
        assert result is not None
        assert result["level"] == "near_ceiling", row["device"]


def test_a_moe_model_reads_only_its_active_share():
    assert bytes_per_token_gb(22_000_000_000, "35B-A3B") == pytest.approx(22 * 3 / 35)
    assert bytes_per_token_gb(17_000_000_000, "27B") == pytest.approx(17.0)


def test_a_slow_dense_setup_is_flagged_far_below():
    """Qwen 27B Q4 (17.5 GB) on a 3090: ceiling ~53 tok/s; 12 tok/s means trouble."""
    result = write_headroom(12.0, 936, 17_500_000_000, "27B", "Q4_K_M")
    assert result["level"] == "far_below"
    assert "GPU offload" in result["reading"]


def test_one_bit_quants_are_not_judged_against_bandwidth():
    """bonsai-27b Q1_0 on a 3090 wrote ~72 tok/s live: 37 % of a ceiling it cannot reach."""
    result = write_headroom(72.0, 936, 4_734_614_604, "27B", "Q1_0")
    assert result["level"] == "compute_bound"
    assert result["ceiling_tps"] == pytest.approx(197.7, abs=0.5)


def test_unknown_bandwidth_gives_no_verdict():
    assert write_headroom(70.0, None, 4_000_000_000, "8B", "Q4_K_M") is None


def test_every_reference_row_points_at_a_known_model_device_and_source():
    from harness_lab_app.local_tuning.reference_benchmarks import DEVICES, MODELS, ROWS

    devices = {d["id"] for d in DEVICES}
    models = {m["id"] for m in MODELS}
    for row in ROWS:
        assert row["device"] in devices, row
        assert row["model"] in models, row
        assert row["source"].startswith("https://"), row
        assert row["tg"] > 0 and row["runtime"]


def test_the_payload_puts_the_scoreboard_in_the_well_tuned_band():
    """Llama 2 7B Q4_0 on NVIDIA cards reaches 60-80 % of the bandwidth ceiling."""
    from harness_lab_app.local_tuning.reference_benchmarks import reference_payload

    rows = [
        r
        for r in reference_payload()["rows"]
        if r["model"] == "llama2-7b-q4_0" and r["device"].startswith("rtx")
    ]
    assert rows and all(0.6 <= r["fraction"] <= 0.85 for r in rows)


def test_a_one_bit_model_sits_far_below_its_bandwidth_ceiling():
    """Bonsai Q1_0 published speeds confirm it is compute-bound, not bandwidth-bound."""
    from harness_lab_app.local_tuning.reference_benchmarks import reference_payload

    spark = next(
        r
        for r in reference_payload()["rows"]
        if r["model"] == "bonsai-27b-q1" and r["device"] == "dgx-spark"
    )
    assert spark["fraction"] < 0.8


def test_served_ids_match_their_reference_model():
    from harness_lab_app.local_tuning.reference_benchmarks import match_reference

    assert match_reference("qwen3:8b") == "qwen3-8b-q4"
    assert match_reference("qwen3:4b") == "qwen3-4b-q4"
    assert match_reference("qwen/qwen3-coder-30b") == "qwen3-coder-30b-a3b-q4"
    assert match_reference("openai/gpt-oss-20b") == "gpt-oss-20b"
    assert match_reference("prism-ml/bonsai-27b") == "bonsai-27b-q1"
