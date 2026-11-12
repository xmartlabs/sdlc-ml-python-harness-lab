# ADR-0076: Each step earns a reward, and the harness is pruned by ablation

- **Status:** Accepted
- **Date:** 2026-11-12
- **Deciders:** Marcos Soto
- **Related:** [ADR-0004](0004-objective-validation-and-multidimensional-score.md),
  [ADR-0005](0005-structured-harness-attribution.md),
  [ADR-0034](0034-score-registry.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  [ADR-0048](0048-a-check-that-did-not-measure-is-not-a-failure.md),
  [ADR-0064](0064-an-experiment-shows-its-uncertainty.md),
  [ADR-0066](0066-harness-and-model-vary-together-only-in-a-full-grid.md),
  [ADR-0075](0075-every-arm-is-measured-by-the-same-instrument.md)

## Context

Every score the lab recorded was taken at the end of a run. A run that solved the
task in step 1 and then spent three more steps re-reading files scored the same as
one that needed all four. Replaying three `tetris-v2` runs step by step (Write/Edit
applied from `steps/step-N.events.jsonl`, the official tests run on each prefix,
the final state byte-identical to the archived workspace for K-019 and K-020)
showed 10/10 after step 1 in all three, with 74%, 71% and 32% of the spend after
that point.

Two consequences:

- **Experiments hit a ceiling.** When both arms pass every check, the success rate
  cannot separate them, and `agent_steps` measures the stop criterion, not the work
  (see `memory/learnings.md`, the twelve-run Tetris corpus).
- **The harness could only grow.** Nothing measured whether a skill earned the
  context it occupies, so nothing could justify removing one.

The question came from reinforcement learning: credit each step with the progress
it made, and ask which part of the harness — or the model alone — made it.

## Decision

**Each step is graded by the case's own checks, after the run.**

- The runner records the workspace after every step: a copy for a scratch
  workspace (`steps/step-N.snapshot/`, installed directories excluded, 50 MB cap),
  a diff against HEAD for a cloned repository (`steps/step-N.diff`), plus
  `steps/step-N.meta.json` with the step clock and, on the SWE lane, its live
  F2P/P2P verdict.
- The worker grades each snapshot with the final validation's spec, executor and
  parser, rewritten to point at the snapshot (ADR-0075). Progress is the share of
  conclusive checks passing (ADR-0048); reward is its change; the trajectory is
  stored on `Validation.objective["per_step"]` — no schema change.
- **Grading happens after the run, never inside the loop.** An agent that could
  read its reward between steps would change behaviour because it is measured, and
  could game the signal. The SWE lane keeps its pre-existing live stop.

**Five scores, all facts:** `steps_to_resolve`, `cost_to_resolve_usd`,
`post_resolution_cost_share` (token share when the model was free),
`progress_auc`, `step_regressions` — `source: step_reward`, in every profile that
reads efficiency. Absent, never zero, for a run without snapshots.

**Credit is co-occurrence and says so.** Each step lists the skills and governance
documents it opened (the attribution parsers, applied per step). The run's and the
corpus's credit tables carry `kind: "co_occurrence"`; they choose what to ablate,
they decide nothing.

**Removal is decided by leave-one-out ablation with an equivalence margin.**
`make prune` creates one experiment per (harness CLI, model): control = full
harness, one treatment per skill with exactly that skill disabled, family prefixed
`prune-`. The summary's `pruning` block reads each pair with case-clustered paired
bootstraps: **removable** only when success loses at most 5 points and
`cost_to_resolve_usd` / `progress_auc` move at most 20% of the control, across the
whole interval; **keep** when an interval sits past a margin; **inconclusive**
otherwise, below three paired cases, or when either metric was not measured on a
paired case. "No difference observed" never removes anything, and neither does a
success rate alone.

**A re-score removes the step-reward scores it no longer emits.** Scores are
upserted by `(name, version)`, so a value an evaluator stops emitting used to
survive. An evaluator that declares `invalidates_missing` owns the registry
names whose `source` is its id; `ScoreService.evaluate_run` deletes those it did
not re-emit, and only for evaluators the profile ran.

## Consequences

- An experiment whose arms both pass everything can still be read: on
  `cost_to_resolve_usd`, and on how much of each arm's spend came after resolution.
- The lab has a concrete, explainable improvement metric for the template — cost
  to resolve, and the post-resolution share, falling over releases — and a
  defensible procedure for making the harness smaller.
- Grading costs checks × steps after each run. Disk grows by one scratch workspace
  per step.
- Known gaps, documented in [the guide](../guide-step-reward.md): a target
  repository's per-step diffs are recorded but not regraded; opencode steps carry no
  cost; runs from before this change have no curve.
- Not done here: using the measured `steps_to_resolve` to stop the loop. That would
  feed the reward back into the run, which this ADR keeps out on purpose; it is a
  separate decision with its own validity question.

## Alternatives considered

- **Check inside the loop and stop on resolution, for every lane.** Cheaper runs,
  but the agent's continuation prompt would then carry a reward it can steer by,
  and arms with different check suites would stop under different rules. Rejected
  for measurement; may return as an explicit stop policy.
- **Replay edits from the event log after the run.** Works for claude (the
  replay that motivated this ADR reproduced the final state byte for byte), but it
  needs GNU tools and re-executed shell, and opencode logs carry no edit content.
  Snapshots work on every lane.
- **A single λ-weighted return.** Rejected: the lab would be choosing a price for
  a token on the reader's behalf. The separate scores state the trade-off without
  one.
