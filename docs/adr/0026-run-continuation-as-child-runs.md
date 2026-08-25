# ADR-0026: A continuation is a child run, not a reopened one

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Marcos Soto
- **Related:** [ADR-0006](0006-auto-incremental-run-ids.md),
  [ADR-0007](0007-run-deletion-semantics.md),
  [ADR-0021](0021-benchmark-artifact-integrity.md),
  [ADR-0023](0023-batch-run-execution.md),
  [guide-run-continuation.md](../guide-run-continuation.md)

## Context

A run is one non-interactive container execution. The step loop in
`scripts/bench_run.py` stops on a stop signal, on a solved SWE instance, or on a
no-progress fingerprint — and then `finalize_workspace` archives
`workspace/<run_id>/` into `data/runs/<run_id>/workspace/` and deletes the live
directory. Nothing survives to continue from.

That makes the most common outcome a dead end. Across 113 runs the loop
exercised 1 step 19 times, 2 steps 22 times, 3 steps 11 times and 4 steps 11
times: it almost always stops early, well under a `max_steps` of 10. The
operator looks at a workspace that is 80% of the way there and has exactly two
options — re-run the same prompt from zero (`clone`), or, only for a
`failed`/`cancelled` run, replay the *original* prompt against the surviving
workspace (`retry`). Neither can say "keep what you have and now do this".

The tempting fix is to reopen the finished run: append steps, extend its logs,
recompute its score. That breaks the thing the lab exists to produce. A run's
`Validation`, `Audit`, `usage.json` and `condition_hash` describe one
measurement under one set of conditions; appending human-authored guidance to it
silently rewrites what was measured, and every artifact already exported from it
becomes a lie.

## Decision

Continuing a run creates a **new run** whose workspace is seeded from the
parent's final state.

- The child gets its own id, `<parent>-C-NNN`, so the chain is legible on disk,
  in logs and in every listing without a join. Ids stay unique against both the
  database and the on-disk `data/runs/` and `workspace/` directories, exactly as
  `_next_run_id` does for the `-K-` lane (ADR-0006).
- `Run.parent_run_id` records the edge; `RunConfig.continuation_prompt` records
  the follow-up instruction. Both are nullable/empty for every run that predates
  this, and no backfill invents lineage that never existed.
- The worker copies `data/runs/<parent>/workspace/` — or the still-live
  `workspace/<parent>/` when the parent died mid-flight — into
  `workspace/<child>/` before starting the container, and skips SWE-bench Pro
  provisioning, which would overwrite exactly what was just seeded.
- The child runs with `--resume`, so `validate_workspace` keeps the seeded tree,
  and `provision_workspace` short-circuits on the existing `.git` rather than
  re-cloning over it. No new runner flags were needed.
- The parent is untouched. Deleting a run that has children is refused without
  `force`, on the same reasoning as ADR-0007: the guard exists so lineage cannot
  be silently orphaned.

The child is a full run, so it owns `data/runs/<child>/` with its own
`steps/step-N.log`, `usage.json` and `metadata.json`. That is what makes this
cheap: no step offsets, no merged token accounting, no artifact invalidation.

**A continuation is a human intervention, and the measurement says so.**
`chain_depth` (0 for an original run, N for the Nth link) is exported on every
leaderboard row and recorded in the run's `effort` dimension alongside
`interventions`. The dashboard hides `chain_depth > 0` by default, behind an
"Include continuations" checkbox. A run that a human steered three times is not
comparable to a one-shot, and the default view must not quietly mix them.

## Consequences

- The lab can now spend a stalled workspace instead of discarding it — including
  the 15 of 32 failures that were 7200s timeouts, where the live workspace is
  intact and only the clock ran out.
- Chains are unbounded: `x-C-001-C-001` is a legal id and simply reports
  `chain_depth = 2`. Ids grow with depth; that is the cost of keeping them
  self-describing.
- The child's `condition_hash` differs from the parent's, because its prompt
  source differs. This is correct — it *is* a different condition — but it means
  a continuation never groups with its parent in provenance comparisons.
- Continuation is offered only from a terminal state and only from the parent's
  final workspace. Branching from an intermediate step is not supported;
  `data/runs/<id>/steps/` holds logs, not snapshots.
- `--fresh` still governs archival, so a child archives and cleans up its own
  workspace the same way any other run does. A chain therefore leaves one
  archived workspace per link, which is deliberate: each link is independently
  inspectable.
