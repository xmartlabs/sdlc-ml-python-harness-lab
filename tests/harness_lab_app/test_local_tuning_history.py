"""History of local-model tests: labels, summaries and noise-aware comparison."""

from __future__ import annotations

from harness_lab_app.local_tuning.contracts import LoadConfig, ServedConfig, TestResult
from harness_lab_app.local_tuning.history import auto_label, compare_metrics, summarize


def _results(
    read: float, write: float, step: float, agent_failures: int = 0, window="ok"
):
    cases = [{"failure": "no_call" if i < agent_failures else ""} for i in range(3)]
    return [
        TestResult(name="fit", verdict="ok", reading=""),
        TestResult(
            name="speed",
            verdict="ok",
            reading="",
            detail={
                "read_tokens_per_second": read,
                "write_tokens_per_second": write,
                "seconds_per_step": step,
            },
        ),
        TestResult(name="window", verdict=window, reading=""),
        TestResult(
            name="agent",
            verdict="ok" if not agent_failures else "tight",
            reading="",
            detail={"cases": cases},
        ),
    ]


def test_the_label_names_what_was_served():
    label = auto_label(
        "prism-ml/bonsai-27b",
        LoadConfig(context_length=32768, flash_attention=True, eval_batch_size=512),
        ServedConfig(context_length=50432, eval_batch_size=2048),
    )
    assert label == "bonsai-27b · 49k · FA · batch 2048"


def test_the_label_marks_kv_choices():
    label = auto_label(
        "m",
        LoadConfig(
            context_length=65536, offload_kv_cache_to_gpu=False, kv_cache_bytes=1.0625
        ),
        None,
    )
    assert "KV in RAM" in label and "KV q8_0" in label


def test_summary_pulls_the_numbers_a_row_needs():
    summary = summarize(
        _results(2000.0, 74.0, 7.0, agent_failures=1),
        ServedConfig(context_length=50432),
        {"breakdown": {"total_gb": 9.3}},
    )
    assert summary["seconds_per_step"] == 7.0
    assert summary["agent_passed"] == 2 and summary["agent_total"] == 3
    assert summary["context_length"] == 50432
    assert summary["estimated_gb"] == 9.3
    assert summary["verdicts"]["agent"] == "tight"


def test_two_runs_of_one_config_are_a_tie_not_a_winner():
    """The user's two live runs: 75.1 vs 75.2 tok/s written is noise."""
    a = summarize(_results(1123.0, 75.1, 7.1), ServedConfig(context_length=50432))
    b = summarize(_results(1117.7, 75.2, 7.0), ServedConfig(context_length=50432))
    result = compare_metrics(a, b)
    assert result["overall"] == "tie"
    assert all(row["winner"] in ("tie", "") for row in result["rows"])
    assert "noise" in result["summary"]


def test_a_real_speed_gap_names_the_winner():
    a = summarize(_results(1000.0, 40.0, 14.0), ServedConfig(context_length=32768))
    b = summarize(_results(2000.0, 74.0, 7.0), ServedConfig(context_length=32768))
    result = compare_metrics(a, b)
    assert result["overall"] == "b"
    step = next(row for row in result["rows"] if row["key"] == "seconds_per_step")
    assert step["winner"] == "b" and step["delta_pct"] == -50.0


def test_a_better_verdict_outweighs_raw_speed():
    """A faster config whose window fails is not the better one."""
    fast_but_small = summarize(_results(3000.0, 90.0, 5.0, window="fail"))
    slower_but_fits = summarize(_results(2000.0, 74.0, 7.0, window="ok"))
    result = compare_metrics(fast_but_small, slower_but_fits)
    assert result["overall"] == "b"
    assert "tests" in result["summary"]


def test_one_agent_case_is_a_real_difference():
    a = summarize(_results(2000.0, 74.0, 7.0, agent_failures=0))
    b = summarize(_results(2000.0, 74.0, 7.0, agent_failures=1))
    row = next(r for r in compare_metrics(a, b)["rows"] if r["key"] == "agent_passed")
    assert row["winner"] == "a"
