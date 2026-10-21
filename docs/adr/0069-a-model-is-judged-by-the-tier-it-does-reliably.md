# ADR-0069: A model is judged by the tier it does reliably

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** Marcos Soto
- **Related:** [ADR-0063](0063-the-context-limit-opencode-is-told-is-read-off-the-serving-host.md),
  [ADR-0064](0064-an-experiment-shows-its-uncertainty.md),
  [ADR-0066](0066-harness-and-model-vary-together-only-in-a-full-grid.md)

## Context

The question teams ask about small and self-hosted models is not "can it?" but "can I
rely on it, and for what?". The lab answered with a pass rate per arm on whatever cases
were run — typically one or two easy ones — which cannot tell a model that solved a
task once from one that solves it every time, nor say how far up in difficulty it goes.
2026 work on agent evaluation points the same way:

- reliability across repeated tries (pass^k) is a different quantity from pass@1;
- identical runs vary more than many agent-model pairs differ;
- most losses of small local models in agent loops are *transport* failures — malformed
  tool calls, unknown tools, context overflow — not reasoning failures, and are fixed by
  the harness or the serving configuration rather than by a bigger model.

Separately, the run forms carried their own copy of the native Claude model list. It
went stale for months — no current-generation model was offered — and nothing checked
it against the gateway, which today has no card for Sonnet 5.5 or Haiku 5.5.

## Decision

1. **Cases can declare a difficulty tier** with a `tier:<name>` tag. When an
   experiment's cases span two or more tiers, its summary carries a `capability` block:
   per arm, each tier's reading and the **frontier** — the highest tier reliable with
   every lower tier reliable too. While a tier has fewer than 4 cases, the block also
   names the shortfall and `frontier_if_enough_cases` — where the frontier would sit if
   every case so far kept its rate — and the panel shows it as **Tier N?**, never as a
   frontier.
2. **"Reliable" has one definition** (`capability.tier_reliability`): at least 4 cases;
   every case passing strictly in at least 4 of 5 runs; and the Wilson 95% lower bound of
   the pooled pass rate at least 0.70. A tier that passes without meeting it is
   *capable*. Three repetitions can show capability, never reliability — 3/3 has a lower
   bound of 0.44. 22 of 25 with every case at 4 or 5 lands at 0.700, inside the rule by
   design; the per-case floor is what keeps one hard case from hiding behind easy ones.
3. **Failures are classified before they are charged to a model**
   (`capability.classify_failure_text`): tool-call schema errors, unknown tools, JSON
   decode errors, context overflow and aborted streams are *transport*; everything else
   is *capability*. The context markers are the ones the context probe already reads.
4. **The clinic ladder** — `clinic-slots-t1`, `clinic-booking-t2`, `clinic-rules-t3` — is
   the first tiered corpus: appointment scheduling, pure Python, with the rules that
   separate careful from hurried builds (half-open intervals, doctor *and* patient
   overlap, atomic reschedule, idempotency, DST, UTC storage, an injected clock, an audit
   trail with no patient data). Calibrated: a plausible naive build scores 5/10, 11/14,
   4/12; a reference scores every test; an empty workspace none.
5. **One native model list** (`native_models.NATIVE_MODELS`) served by
   `GET /api/models/native`, each model ordered by generation and size and marked
   `on_gateway` true/false/null against the gateway's cards. The forms read it; a model
   the gateway cannot serve is labelled before launch.
6. **A harness ladder is a valid harness x model grid**: three or more harness levels
   that can be ordered so each changes exactly one field from the one before (none →
   governance → governance + skills) are accepted, compared against the first level.
   Two levels with two fields moving together remain a refused confound.
7. **Context below 64k is advised, not blocked.** The 32k floor stays the hard gate;
   between 32k and 64k the probe returns an `advisory` that Settings shows.

## Consequences

- The lab can say "Qwen 27B through opencode does tier 1 reliably and tier 2 with the
  full harness only" — a claim a team can act on — and whether the harness moved the
  frontier.
- The partial-vs-full question (does governance help without skills? do skills add on
  top?) runs as one experiment per model set, with intervals.
- A reliable tier needs 4 cases x 5 runs per arm: 20 runs per tier per arm. The clinic
  ladder has one case per tier; a reliable frontier needs three more cases per tier,
  which is the corpus work this enables rather than finishes.
- The native list still has to be edited when models ship; the difference is that it
  is edited once and a test holds it to the gateway.
