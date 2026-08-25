# Continuing a run

A run is one non-interactive container execution. When its step loop stops —
because the agent signalled it was done, because a SWE instance resolved, or
because the workspace stopped changing — the run finishes and its workspace is
archived. In practice that happens early: across the first 113 runs the loop
used 1 step 19 times and 2 steps 22 times, against a configured budget of 10.

**Continue** takes the state a run left behind and starts a new run from it,
driven by a follow-up prompt you write. The original run is never modified.

## How to continue a run

1. Open the run's detail page. The **Continue** panel appears once the run has
   stopped — `completed`, `awaiting_manual_score`, `failed` or `cancelled`.
2. Write the follow-up prompt. Say what to do next, not what the case was: the
   agent is told, in the prompt, that the workspace is the product of an earlier
   session and is its to build on.
3. Pick a step budget (it defaults to the parent's) and press **Continue run**.

A child run appears immediately, named after its parent:
`tetris-v1-K-063` → `tetris-v1-K-063-C-001`. Continue that one and you get
`tetris-v1-K-063-C-001-C-001`. Continuing the same parent twice gives you two
siblings — `-C-001` and `-C-002` — each starting from the same state, which is
how you compare two directions from one point.

The header of every run in a chain links up to its parent and down to its
continuations.

## What the child inherits

- **The workspace.** Copied from `data/runs/<parent>/workspace/`, or from the
  still-live `workspace/<parent>/` when the parent died before archiving — a
  7200s timeout, a cancel mid-step. That second case is the one most worth
  rescuing.
- **The arm.** Harness, planner and executor models, prompt variant, dry-run
  flag, and the skills ablation. The prompt is the only deliberate difference.
- **Nothing else.** The child owns its own `data/runs/<id>/` with its own step
  logs, `usage.json`, validation and score. Cost and steps are never merged
  across a chain.

## Continue, retry, or run again?

| You want to… | Use | What happens |
|---|---|---|
| Give the agent a **new instruction** on top of what it built | **Continue** | New child run, workspace seeded from this one |
| Re-run **the same prompt** because the run died mid-flight | **Retry** | Same run, same id, resumes its own workspace |
| Get **another independent trial** of the same arm | **Run again** (clone) | New run, empty workspace, original case prompt |

Retry is for a run the lab broke. Continue is for a run the agent finished, or
got far enough into, and that you want to push further.

## What it costs you in measurement

A continuation is a human intervention. Somebody read the workspace and wrote
the next instruction — help a one-shot run never got.

The lab records this rather than hiding it:

- Every leaderboard row carries `chain_depth` (0 for an original run, 1 for its
  continuation, and so on) and `parent_run_id`.
- The Dashboard **hides continuations by default**. Tick *include
  continuations* to see them; when you do, read the ranking as "how far can this
  harness get with help", not "how good is this harness".
- The run's `effort` dimension records `continuations` alongside `steps` and
  `interventions`.

Use continuations to get work done and to explore what a harness could do with
guidance. Do not use them to build the number you publish.

## Limits

- You continue from a run's **final** state. There are no per-step snapshots, so
  branching from step 2 of a five-step run is not possible.
- A run whose workspace was deleted cannot be continued — the panel will say so.
- Deleting a run that has continuations is refused; delete the children first,
  or force the delete knowingly.
- A continuation of a curated benchmark run (Tetris) uses your prompt for its
  first step and the benchmark's own continuation framing for the rest.

Design rationale: [ADR-0026](adr/0026-run-continuation-as-child-runs.md).
