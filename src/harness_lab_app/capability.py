"""Can this model *reliably* do this tier of task? (ADR-0069)

A small or self-hosted model that passes a case once has shown it *can*; a team
choosing it needs to know it *does*. This module turns repeated runs on cases
grouped into difficulty tiers into that answer:

- **A tier is reliable** for a model-harness pair when every case passes strictly
  in at least 4 of 5 runs (80% per case, so one hard case cannot hide behind easy
  ones) *and* the Wilson 95% lower bound of the pooled pass rate is at least 0.70.
  With 20 runs that means 19 or more; with 25, 23 or more. Three repetitions can
  show capability but never reliability: 3 of 3 has a lower bound of 0.44.
- **The capability frontier** is the highest tier reached with no unreliable tier
  below it. While a tier has fewer than four cases the frontier cannot move, so
  the reading also names the shortfall and where the frontier *would* sit on the
  runs so far — a suite still being built shows its direction, never a verdict.
- **Failures are classified** before they count against a model. A malformed tool
  call, an unknown tool or a context overflow means the pair could not *talk*
  ("transport"), not that the model could not *solve* ("capability"). Studies of
  small local models in 2026 find transport failures are the larger share, which
  is a harness or serving fix, not a reason to drop the model.

Pure functions; the numbers here are descriptive, never a significance test.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

from harness_lab_app.statistics import wilson_interval

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

RELIABLE = "reliable"
CAPABLE = "capable"
NOT_ESTABLISHED = "not_established"

TRANSPORT = "transport"
CAPABILITY = "capability"

MIN_CASES = 4
MIN_RUNS_PER_CASE_FOR_RELIABILITY = 5
PER_CASE_FLOOR = 0.8  # 4 of 5
WILSON_FLOOR = 0.70
PASS_HAT_K = 3

# The words a broken model-harness conversation leaves behind. Matched
# case-insensitively over a bounded excerpt, never a backtracking pattern.
_TRANSPORT_MARKERS = (
    # AI SDK (opencode) and tool-call schema errors
    "invalidtoolinputerror",
    "invalid input for tool",
    "nosuchtoolerror",
    "unavailable tool",
    "tool not found",
    "unknown tool",
    "invalid tool call",
    "tool_call",  # malformed tool_call payloads
    "cannot unmarshal",
    "jsondecodeerror",
    "expected value: line",
    # context overflow, as LM Studio / llama.cpp / Ollama / OpenAI-shaped
    # backends phrase it (the same markers the context probe reads)
    "n_ctx",
    "context length",
    "context_length_exceeded",
    "context window",
    "too many tokens",
    "maximum context length",
    # a stream cut mid-answer
    "stream aborted",
    "stream terminated",
)
_MAX_TEXT = 20_000
_WHITESPACE = re.compile(r"\s+")


def classify_failure_text(text: str | None) -> str | None:
    """``transport``, ``capability`` or ``None`` for a failed run's evidence.

    ``text`` is what the run left behind — the failing check's output and the
    tail of the agent log. A transport marker anywhere wins: once the
    conversation broke, a wrong answer after it says nothing about the model.
    """
    excerpt = _WHITESPACE.sub(" ", (text or "")[-_MAX_TEXT:]).strip().lower()
    if not excerpt:
        return None
    if any(marker in excerpt for marker in _TRANSPORT_MARKERS):
        return TRANSPORT
    return CAPABILITY


def _pass_hat_k(outcomes: Sequence[bool], k: int) -> float | None:
    runs = len(outcomes)
    if runs < k:
        return None
    passes = sum(bool(v) for v in outcomes)
    return math.comb(passes, k) / math.comb(runs, k)


def tier_reliability(cases: Mapping[str, Sequence[bool]]) -> dict[str, Any]:
    """Reliability of one tier from every case's strict pass/fail per run."""
    scored = {case: list(runs) for case, runs in cases.items() if runs}
    runs = sum(len(r) for r in scored.values())
    passed = sum(sum(bool(v) for v in r) for r in scored.values())
    low_high = wilson_interval(passed, runs) if runs else None
    wilson_low = low_high[0] if low_high else None
    per_case = {case: sum(bool(v) for v in r) / len(r) for case, r in scored.items()}
    hats = [
        h
        for h in (_pass_hat_k(r, PASS_HAT_K) for r in scored.values())
        if h is not None
    ]
    weak = sorted(case for case, rate in per_case.items() if rate < PER_CASE_FLOOR)
    short = sorted(
        case for case, r in scored.items() if len(r) < MIN_RUNS_PER_CASE_FOR_RELIABILITY
    )
    capable = bool(scored) and all(rate > 0 for rate in per_case.values())

    if len(scored) < MIN_CASES:
        status, reason = (
            NOT_ESTABLISHED,
            f"{len(scored)} case(s) scored; a tier needs at least {MIN_CASES} cases.",
        )
    elif weak:
        status, reason = (
            NOT_ESTABLISHED,
            f"Below {round(PER_CASE_FLOOR * 100)}% on: {', '.join(weak)}.",
        )
    elif short:
        status, reason = (
            CAPABLE,
            f"Every case passed, but {', '.join(short)} had fewer than "
            f"{MIN_RUNS_PER_CASE_FOR_RELIABILITY} runs — capable, not yet shown reliable.",
        )
    elif wilson_low is None or wilson_low < WILSON_FLOOR:
        status, reason = (
            CAPABLE,
            f"Pooled pass rate's 95% lower bound is {wilson_low:.2f}, below "
            f"{WILSON_FLOOR:.2f}.",
        )
    else:
        status, reason = (
            RELIABLE,
            (
                f"Every case at ≥{round(PER_CASE_FLOOR * 100)}% and the pooled 95% lower "
                f"bound is {wilson_low:.2f}."
            ),
        )
    return {
        "status": status,
        "reason": reason,
        "capable": capable,
        "cases": len(scored),
        "runs": runs,
        "passed": passed,
        "pass_rate": passed / runs if runs else None,
        "wilson_low": wilson_low,
        "per_case": per_case,
        # Every case scored so far clears the per-case floor; says nothing about
        # whether there are enough cases to call the tier reliable.
        "runs_ok": bool(scored) and not weak,
        f"pass_hat_{PASS_HAT_K}": sum(hats) / len(hats) if hats else None,
    }


def capability_frontier(
    tiers: Mapping[str, Mapping[str, Sequence[bool]]], *, order: Sequence[str]
) -> dict[str, Any]:
    """The highest tier reliable with every lower tier reliable too."""
    readings = {tier: tier_reliability(tiers.get(tier, {})) for tier in order}
    frontier = None
    for tier in order:
        if readings[tier]["status"] != RELIABLE:
            break
        frontier = tier
    provisional = None
    for tier in order:
        if not readings[tier]["runs_ok"]:
            break
        provisional = tier
    shortfall = {
        tier: MIN_CASES - readings[tier]["cases"]
        for tier in order
        if readings[tier]["cases"] < MIN_CASES
    }
    return {
        "frontier": frontier,
        "order": list(order),
        "tiers": readings,
        "case_shortfall": shortfall,
        "frontier_if_enough_cases": provisional,
    }
