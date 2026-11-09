"""Turn test sessions into a comparable history (pure, no I/O).

Every finished test session is kept as a profile so two configurations can be
read side by side later. This module owns the three judgements that make that
history readable: what a profile is called, which numbers summarise it, and
when a difference between two profiles is a difference at all.
"""

from __future__ import annotations

from typing import Any, Literal

from harness_lab_app.local_tuning.contracts import (
    LoadConfig,
    ServedConfig,
    TestResult,
)

# Two runs of one configuration on one machine differ by a few percent in
# speed (observed live: 75.1 vs 75.2 tok/s written, 1123 vs 1118 read). A
# smaller gap is noise and must not crown a winner.
NOISE_FRACTION = 0.05

Better = Literal["higher", "lower"]

# (key, label, which direction is better). Seconds per step is what a long
# agent run actually pays, so it leads.
METRICS: tuple[tuple[str, str, Better], ...] = (
    ("seconds_per_step", "Seconds per agent step", "lower"),
    ("read_tokens_per_second", "Read speed (tok/s)", "higher"),
    ("write_tokens_per_second", "Write speed (tok/s)", "higher"),
    ("agent_passed", "Agent cases passed", "higher"),
    ("context_length", "Served context", "higher"),
    ("estimated_gb", "Estimated memory (GB)", "lower"),
)

_VERDICT_RANK = {"ok": 3, "tight": 2, "inconclusive": 1, "not_applicable": 1, "fail": 0}


def _short_context(tokens: int | None) -> str:
    if not tokens:
        return "?"
    return f"{round(tokens / 1024)}k"


def auto_label(model_id: str, config: LoadConfig, served: ServedConfig | None) -> str:
    """A name that says what differs between two tests of one model.

    Built from the *served* values when known, since those are what ran.
    """
    served = served or ServedConfig()
    context = served.context_length or config.context_length
    flash = (
        served.flash_attention
        if served.flash_attention is not None
        else config.flash_attention
    )
    kv_on_gpu = (
        served.offload_kv_cache_to_gpu
        if served.offload_kv_cache_to_gpu is not None
        else config.offload_kv_cache_to_gpu
    )
    batch = served.eval_batch_size or config.eval_batch_size
    parts = [model_id.rsplit("/", 1)[-1], _short_context(context)]
    if flash:
        parts.append("FA")
    if kv_on_gpu is False:
        parts.append("KV in RAM")
    kv_type = {2.0: "", 1.0625: "KV q8_0", 0.5625: "KV q4_0"}.get(
        config.kv_cache_bytes, f"KV {config.kv_cache_bytes} B"
    )
    if kv_type:
        parts.append(kv_type)
    if batch:
        parts.append(f"batch {batch}")
    if config.gpu_offload < 1:
        parts.append(f"GPU {round(config.gpu_offload * 100)}%")
    return " · ".join(parts)


def summarize(
    results: list[TestResult] | list[dict],
    served: ServedConfig | dict | None = None,
    estimate: dict | None = None,
) -> dict[str, Any]:
    """The handful of numbers a history row and a comparison need."""
    rows = [r if isinstance(r, dict) else r.model_dump() for r in results]
    by_name = {row.get("name"): row for row in rows}
    speed = (by_name.get("speed") or {}).get("detail") or {}
    agent_cases = ((by_name.get("agent") or {}).get("detail") or {}).get("cases") or []
    served_dict = (
        served.model_dump() if isinstance(served, ServedConfig) else (served or {})
    )
    breakdown = (estimate or {}).get("breakdown") or {}
    return {
        "verdicts": {name: row.get("verdict") for name, row in by_name.items()},
        "seconds_per_step": speed.get("seconds_per_step"),
        "read_tokens_per_second": speed.get("read_tokens_per_second"),
        "write_tokens_per_second": speed.get("write_tokens_per_second"),
        "agent_passed": (
            sum(1 for case in agent_cases if not case.get("failure"))
            if agent_cases
            else None
        ),
        "agent_total": len(agent_cases) or None,
        "context_length": served_dict.get("context_length"),
        "estimated_gb": breakdown.get("total_gb"),
    }


def _winner(a: float | None, b: float | None, better: Better) -> str:
    """ "a", "b", "tie" (within noise) or "" (not measured on both)."""
    if a is None or b is None:
        return ""
    if a == b:
        return "tie"
    scale = max(abs(a), abs(b))
    if scale and abs(a - b) / scale < NOISE_FRACTION:
        return "tie"
    a_better = a > b if better == "higher" else a < b
    return "a" if a_better else "b"


def compare_metrics(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Row-by-row comparison of two summaries, plus a plain-language verdict."""
    rows = []
    wins = {"a": 0, "b": 0}
    for key, label, better in METRICS:
        av, bv = a.get(key), b.get(key)
        # Integer counts (agent cases) are exact; noise only blurs measured rates.
        winner = (
            ("tie" if av == bv else ("a" if (av or 0) > (bv or 0) else "b"))
            if key == "agent_passed" and av is not None and bv is not None
            else _winner(av, bv, better)
        )
        if winner in wins:
            wins[winner] += 1
        delta_pct = (
            round((bv - av) / av * 100, 1)
            if isinstance(av, (int, float)) and isinstance(bv, (int, float)) and av
            else None
        )
        rows.append(
            {
                "key": key,
                "label": label,
                "better": better,
                "a": av,
                "b": bv,
                "delta_pct": delta_pct,
                "winner": winner,
            }
        )
    # A verdict that got worse (e.g. window ok -> fail) outweighs speed.
    verdict_rows = []
    for name in ("fit", "speed", "window", "agent"):
        va = (a.get("verdicts") or {}).get(name)
        vb = (b.get("verdicts") or {}).get(name)
        if va is None and vb is None:
            continue
        ra, rb = _VERDICT_RANK.get(va or "", -1), _VERDICT_RANK.get(vb or "", -1)
        verdict_rows.append(
            {
                "name": name,
                "a": va,
                "b": vb,
                "winner": "tie" if ra == rb else ("a" if ra > rb else "b"),
            }
        )
    verdict_wins = {
        side: sum(1 for row in verdict_rows if row["winner"] == side)
        for side in ("a", "b")
    }
    if verdict_wins["a"] != verdict_wins["b"]:
        overall = "a" if verdict_wins["a"] > verdict_wins["b"] else "b"
        why = "it passes more of the tests"
    elif wins["a"] != wins["b"]:
        overall = "a" if wins["a"] > wins["b"] else "b"
        why = "it wins more of the measured numbers"
    else:
        overall = "tie"
        why = ""
    if overall == "tie":
        summary = (
            "No meaningful difference: every measured gap is within the "
            f"{round(NOISE_FRACTION * 100)}% run-to-run noise, and the test "
            "verdicts match. Keep the cheaper one (less memory)."
        )
    else:
        summary = f"{overall.upper()} is better: {why}."
    return {
        "rows": rows,
        "verdicts": verdict_rows,
        "overall": overall,
        "summary": summary,
        "noise_pct": round(NOISE_FRACTION * 100),
    }
