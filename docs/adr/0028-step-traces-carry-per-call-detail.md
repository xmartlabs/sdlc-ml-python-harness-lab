# ADR-0028: Step traces carry per-call detail parsed from run artifacts

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Marcos Soto
- **Related:** [ADR-0027](0027-langfuse-traces-rebuilt-from-run-artifacts.md),
  [guide-langfuse-observability.md](../guide-langfuse-observability.md),
  [plans/2026-08-langfuse-observability.md](../plans/2026-08-langfuse-observability.md)

## Context

ADR-0027 shipped one span per step whose output was a 24k-char log tail. A run
like `tetris-v1-K-004` — 85 turns, 84 tool calls, six minutes — flattened to
two spans, and the operator's question ("which call burned the tokens, which
edit looped, what model actually answered") had no answer in the trace.

The detail was already on disk and simply not read:

- The claude lane tees raw stream-json to `steps/step-N.events.jsonl`: every
  assistant message with its real model and per-turn usage, every
  `tool_use`/`tool_result` pair, and a terminal `result` event with the step's
  true tokens, cost, `stop_reason` and latency.
- The opencode lane's `--print-logs` step log brackets every LLM call between
  a `message=stream providerID=… modelID=…` line and the next `message=loop`
  line — both with real wall-clock timestamps and an `agent` label.

Two honest gaps in those artifacts shaped the decision:

1. claude stream events carry **no timestamps**, and their per-message
   `output_tokens` are **streaming snapshots** that undercount the finished
   message (summing them gives 1.3k where `usage.json` says 27k).
2. `run_id` alone is not globally unique: the same id can name two different
   runs (a container-volume copy and the local `data/` copy), and ADR-0027's
   deterministic ids made those two runs silently overwrite each other in
   Langfuse.

## Decision

**Each step trace gets child observations parsed from whichever artifact its
lane wrote, with imprecision labeled rather than hidden.**

- `observability/step_detail.py` is a pure parser: claude events →
  *generations* (one per assistant message id) + *tools* (one per
  `tool_use`/`tool_result` pair, unresolved calls kept and flagged); opencode
  logs → *generations* (one per `stream`→`loop` window). No I/O, no clock, no
  config — the whole shape is dictionary-assertable in tests.
- **Timing is labeled at the source.** opencode windows use the log's own
  clocks (`timing_source: log`). claude events use `_received_at` when present
  (`events`); without it they are spread evenly across the step window and
  every affected observation says `interpolated`. `bench_run.py` now stamps
  `_received_at` into each teed event line, so the label upgrades itself for
  all future runs while old files remain exportable.
- **Usage is labeled at the source.** Per-generation claude usage keeps the
  max stream snapshot per counter and marks `output_tokens_source:
  stream_snapshot`; the step span's totals come from the terminal `result`
  event (tokens, cost, `stop_reason`, `ttft_ms`, turn count), and `usage.json`
  stays the run-level total of record.
- **The step's output becomes its outcome** — the final assistant message when
  the artifacts record one — and the log tail moves to a dedicated `log`
  child observation.
- **Ids are salted with `condition_hash`** (falling back to `created_at`):
  `sha256(run_id@salt:key)`. Re-exporting the same run stays idempotent;
  distinct runs sharing a `run_id` stop colliding.

## Consequences

- A claude step is now ~150 observations instead of one; child payloads are
  capped far tighter than step payloads (2–8k chars) to keep a run's export in
  the hundreds of KB.
- Historical runs backfill with `timing_source: interpolated` on claude
  children; only runs recorded after the `_received_at` stamp get real
  per-call clocks. Both are visible in the metadata, never guessed at.
- The id salt changes every previously exported trace id once: the first
  re-export after this change writes fresh traces alongside the old ones in
  the same session. Cosmetic, one-time, and the price of ending silent
  overwrites.
- Exact prompts, exact per-call output tokens and per-call cost remain the
  gateway's to provide (Layer B of the plan) — nothing on disk records them,
  and this ADR does not pretend otherwise.
