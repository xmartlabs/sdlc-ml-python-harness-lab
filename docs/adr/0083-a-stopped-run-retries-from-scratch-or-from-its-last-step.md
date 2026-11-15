# ADR-0083: A stopped run is retried from scratch or from its last completed step

- **Status:** Accepted
- **Date:** 2026-11-15
- **Deciders:** Marcos Soto
- **Related:** [ADR-0006](0006-auto-incremental-run-ids.md),
  [ADR-0026](0026-run-continuation-as-child-runs.md),
  [ADR-0076](0076-per-step-reward.md),
  [ADR-0079](0079-a-hard-case-is-paced-in-phases-and-cannot-read-its-grade.md),
  [ADR-0082](0082-models-are-compared-on-one-case-by-checks-not-pass-rate.md)

## Context

A retry had one meaning: run the same id again from step 1, over whatever
workspace the attempt left (`--resume`). For a phased case that combines the
costs of both other options. It pays again for every phase already done:
chess3d-v1-K-008 had finished three of five phases, worth about US$5.50 and 40
minutes. And it does not start clean either: the agent finds the half-written
state of the step that was cut, and its record mixes two attempts. The first
step's files are overwritten, the times and costs add up across both.

Runs on the 3D cases are cut by things that are not the model. K-008 was stopped
by a worker restart. K-003 was stopped by a gateway that stopped answering.
What finished before the cut is a valid measurement of the model, and throwing it
away to rerun it measures nothing new.

A continuation (ADR-0026) is the other existing tool, and it does not fit: it is
a child run with a prompt someone wrote, counted as a human intervention and
hidden from comparisons.

## Decision

1. **A stopped run (failed or cancelled) offers two retries**, under the same id:
   - **From scratch** (`POST /api/runs/{id}/retry?mode=scratch`). The attempt is
     discarded: the workspace (live and archived), the steps, usage, logs, and
     the validation, attribution, audit, artifact and score rows. The run then
     runs every step as if it were new. The run's configuration
     (`case_spec.json`, masks, staged harness files) and its event log are kept.
   - **From the last completed step** (`mode=continue`). The workspace is
     restored from that step's snapshot, and the run executes only the steps
     after it (`bench_run.py --from-step N`). The finished steps are kept as they
     ran: their events, logs, snapshots, time and per-step grade. Their seconds
     and prompt kinds are carried into the run's totals. Whatever the interrupted
     step left behind is dropped, both its partial artifacts and its changes to
     the workspace, because nobody can say whether that state is sound.
2. **A step is completed** when its `step-N.meta.json` exists and is a
   `snapshot`, and every step before it is on record too. A gap, a `diff`
   snapshot (a cloned repository), a missing copy, or a run that used every step
   leaves only "from scratch". `GET /api/runs/{id}/retry-options` says which step
   it would continue from or why it cannot, and the run page shows that as the
   button's label or tooltip.
3. **The original retry stays** as `mode=resume`, the default of the endpoint,
   so scripts that call it keep their behaviour. The run page no longer offers
   it.
4. **A continued run says so.** Its event log records the step it continued
   from and which snapshot was restored, and `bench_run.py` prints the kept steps
   and their minutes. If the record of an earlier step is missing when the
   runner starts, it runs every step instead, loudly, rather than filing a
   trajectory it cannot account for.

## Consequences

- K-008 can finish its last two phases for the price of those two phases.
- A continued run is one trajectory with a seam: steps before N ran in one
  attempt, the rest in another. The agent cannot tell, because each step already
  starts from the workspace alone with no conversation carried over. Wall time
  is still the sum of the step clocks, so the pause between attempts is never
  counted.
- Continuing is a judgment about why the run stopped, and the lab does not make
  it for you. Continue a run cut by infrastructure: a restart, a dead worker, a
  gateway that stopped answering. Do not continue one cut by its own time
  budget, because the time it had is part of what is measured. Do not continue
  one that finished and failed checks either, because that is the result.
  Classifying the cause and offering only the right button is left for later.
- `resume_from_step` is a new `runconfig` column, 0 for every earlier run. The
  command a run is built with changes only on a continued retry.
