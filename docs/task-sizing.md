# Task Sizing: matching the chunk to the model and the tool

How big a unit of work should one agent turn take? The answer depends on **which
model tier** runs it and **which tool** drives it. Getting this wrong is the main
reason a self-hosted model produces a very long interaction and no working result.

This doc captures the heuristics. The runtime levers that go with it (context
window, temperature, model choice) live in [`.env.example`](../.env.example) and
[`.github/portability.md`](../.github/portability.md).

## Lessons learned (the Tetris run)

The example prompt in [`vibe-coding-tetris-example.md`](vibe-coding-tetris-example.md)
("build a fully playable Tetris") was run on OpenCode with self-hosted models:
`qwen3-coder-30b` as executor, and a second pass with Nemotron Super (cloud) as
planner + Qwen as executor. Both **failed the same way: long interaction, no
working game** — while strong cloud models (Copilot, Codex) converged.

The causes were not the prompt. They were sizing and runtime:

- **Context collapse.** A local model served at a 4k-8k window cannot hold
  governance + the skills catalog + the plan + history, so it loses the task.
- **Task too big for one turn.** A full clean-architecture game (~8 files across
  domain/application/infrastructure + tests + docs) is too much for a weak model
  to emit coherently in one go.
- **Planner↔executor gap.** A strong 120B planner wrote steps that assumed a
  strong executor; the weak local executor couldn't chew them.
- **No runnable milestone first.** The gate was `make check` (lint/type/test), not
  "does it launch?". Weak models converge when they get something *running* early,
  then expand — not when they chase architectural completeness up front.

The takeaway is general, not about Tetris: **size each step to the executor, and
make the first deliverable the smallest thing that runs.**

## Chunk size by tier

A "chunk" is one unit of work handed to one agent turn. Keep it independently
verifiable (it compiles / a test passes / it runs).

| Tier | Role | Cloud-strong model | Self-hosted weak model |
|---|---|---|---|
| `planner` | plan, review | A whole feature plan; multi-file review | A short plan (<= ~5 steps), each step one file |
| `executor` | implement, test | A cohesive module (a few related files) | **One file, or one function, per turn** |
| `fast` | docs, quick edits | A doc page; several small edits | One section / one edit |

Rule of thumb for weak self-hosted: **one file per Build turn, run or test it,
then move on.** If a step can't be stated as "produce/modify this one file and
verify it", it is too big — split it.

## Complexity tiers (don't always pay the clean-arch tax)

The full 5-phase SDLC and clean-architecture layering exist for durable features.
For small or throwaway work they add files and indirection a weak model must hold
in its head. Pick the lightest tier that fits:

| Tier | When | Structure | Process |
|---|---|---|---|
| `spike` | greenfield, exploratory, throwaway, "make it run" | **Flat**, 1-3 files, no layer split | Skip phases; runnable milestone first, then iterate |
| `feature` | durable change to the product | Clean architecture (domain/application/infrastructure) | Full 5-phase SDLC + gates |
| `system` | cross-module / architectural | Clean architecture + contracts | Full SDLC; consider a migration plan |

A playable-game prototype is a `spike`: a flat `main.py` that opens a window and
drops a piece beats a perfect 8-file tree that never runs. Promote a `spike` to
`feature` (introduce layers, tests, docs) only once it works and is worth keeping.

## The "first runnable milestone" loop (weak models especially)

For any executable deliverable (game, CLI, service):

1. Define the **smallest artifact that runs** — not the smallest that's correct.
   (Tetris: a window opens and one piece falls under gravity.)
2. Build only that. Run it. Confirm it runs.
3. Add **one** capability (input → states → line clear → levels → menu). Run again.
4. Repeat. Each loop ends with something that still runs.

"Run before you expand" is the informal gate for weak models — cheaper and more
reliable than asking them to satisfy `make check` on a large unrun tree. Layers,
tests, and docs come once the behavior is real.

## Task × tool/model matrix

| Driver | Give it… | Avoid |
|---|---|---|
| Claude Code (cloud, sub-agents) | Whole `feature`/`system`; trust phase fan-out | — |
| OpenCode + cloud planner | `feature`-sized; plan big, execute in one model | — |
| OpenCode + self-hosted weak | `spike` tier; one-file chunks; runnable-first loop | A whole `feature` in one turn; strong-planner/weak-executor split |
| Codex / Antigravity / Copilot (collapsed) | Medium chunks; ask for a plan explicitly first | Relying on automatic phase ordering |

When the driver is a weak self-hosted model, **do not** start from the full
5-phase `feature` flow. Start from a `spike`, reach a runnable milestone, and only
then decide whether to promote it.

When the driver is a small self-hosted model via Copilot, this `spike` + one-file +
runnable-first discipline is named **`local_model_32k`** and is enforced mechanically (a
gateway output cap), not just by these heuristics — see the runtime-config doc below.

## See also

- [`LOCAL_AGENT.md`](../LOCAL_AGENT.md) — the short always-loaded operating contract for the
  `local_model_32k` mode.
- [`docs/local-model-runtime-config.md`](local-model-runtime-config.md) — the mechanical layer
  (gateway `max_tokens` cap, served context, VS Code settings) that makes these heuristics
  binding instead of advisory.
- [`.github/portability.md`](../.github/portability.md) — tiers, self-hosted
  convergence checklist.
- [`.env.example`](../.env.example) — context length, temperature, model choice.
- [`.github/skills/plan_and_execute_feature.md`](../.github/skills/plan_and_execute_feature.md)
  — small-context / weak-model execution mode and the execution backlog format.
