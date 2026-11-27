# ADR-0102: A model plays a fixed game as a lab case

- **Status:** Accepted
- **Date:** 2026-11-26
- **Scope:** `benchmarks/racing-play-v1`, `racing_play` evaluator; builds on ADR-0100
- **Design:** [racing play case](../superpowers/specs/2026-11-26-racing-play-case-design.md)

## Context

ADR-0100 gave the lab a fixed racing game a model can drive, but only an LM Studio
model, called directly by the worker, recorded as a `GameplayEpisode` outside the
Runs flow. Every other comparison the lab makes — models, harness levels,
repetitions, Compare, Metrics — works on Runs. We want any configured model
(Claude, Codex, Gemini, Antigravity, OpenCode over a local server) to play, and the
result to be a Run.

## Decision

The game is a case. `racing-play-v1` seeds the workspace with a `race` tool
(`race state`, `race step '<controls>'`, `race rules`) and the agent plays through
it in its ordinary harness session. The tool keeps no process: every call replays
an append-only `.race/actions.jsonl` through the shared engine
(`frontend/src/gameplay/racing.mjs`) under a frozen condition (seed 7, 0.5 s per
decision, 60 s horizon). A hidden grader replays the same log offline with the
lab's own engine and condition, emits one validation check per rung (valid episode,
25/50/75 % of the lap, all gates, finished, clean, under par), and writes
`.lab-grade/racing.json`. The `racing_play` evaluator turns that into racing
scores and a play-mode label.

**Scripting is allowed and labelled.** An agent may write a program that calls
`race step`. The run is labelled `played` when the harness's tool events show one
`race step` call per decision, `scripted` otherwise; for a harness with no tool
events (OpenCode) a burst of steps under a second apart is scripted. The label
names its source (`events`/`timing`) and is `unknown` when there is nothing to
read. Forbidding scripts was rejected: the agent has a shell, so a ban could only
be detected, and a score that silently mixed both was rejected as worse.

## Alternatives rejected

- **The lab calls the model each step** (a policy per provider, a new runner
  adapter). A CLI subscription start per decision is minutes per episode, every
  provider needs its own integration, and the harness step loses its meaning.
- **The agent writes a driver program**, graded by running it. That measures
  coding — closer to `racing3d-v1` — not playing.

## Consequences

- Every harness and model the lab runs can play with no per-provider code;
  harness levels and repetitions apply unchanged.
- An episode is ~120 tool round trips: slow and token-heavy. 0.5 s decisions
  (not the 0.2 s of the `/gameplay/racing` page) halve that.
- The engine is readable in the workspace. Look-ahead with it while still issuing
  one `race step` per decision counts as played.
- `racing.mjs` is unchanged; the 60 s horizon lives in the case's
  `condition.mjs`, shared by tool and grader. A test pins the starter's engine
  copy to the frontend's.
- Humans playing under the same condition is deferred.
