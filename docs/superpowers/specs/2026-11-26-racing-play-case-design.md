# Racing play case — design

Date: 2026-11-26 · Branch: `feat/gameplay-lab-case` (from `main` afbde7a)

## Intent

Today the lab can run a model on a *coding* case (tetris-v2, chess3d, racing3d):
a harness CLI writes code and validation grades it. The fixed racing game merged in
PR #64 (`/gameplay/racing`) lets a model *play* a game, but only an LM Studio
model, outside the Runs flow, in its own `GameplayEpisode` table.

The goal is a lab case where you pick any configured model — Claude, Codex,
Gemini, Antigravity, OpenCode over LM Studio/Ollama — and it plays the racing game
as an ordinary Run: chosen in "Which case?", visible in Runs, Metrics, Results and
Compare, with repetitions and harness levels working as for any case.

Why: reuse the lab's whole run/compare machinery for a new kind of capability
(acting step by step in a deterministic environment), instead of a parallel
gameplay subsystem with per-provider integrations.

## Goals

- A new case `racing-play-v1` in `benchmarks/`, selectable in every guided flow.
- Any harness/model the lab can already run can play it, with no per-provider code.
- An objective, deterministic grade from a verified replay of the actual episode.
- Each Run labelled **played** or **scripted** (see below), shown wherever the
  run's result is shown.

## Non-goals

- Cooking, ghosts, the multi-model comparison dashboard and the Codex subscription
  policy from Mauricio's branch `codex/gameplay-checkpoint-2026-11-26`. That branch
  stays his; nothing is cherry-picked from it.
- Removing or migrating the existing `/gameplay/racing` page and `GameplayEpisode`.
  It keeps working as it is.
- Vision/pixel observations. The agent sees the numeric state only.
- Human play in this case (deferred by the user on 2026-11-26). The existing
  `/gameplay/racing` keyboard mode is untouched; recording humans under the case's
  condition and showing them next to runs is a later step.

## Anti-goals

- A score that hides *how* it was obtained: a scripted driver must never be
  indistinguishable from a played one.
- A second simulation. The grade must replay the exact engine the browser and the
  game runtime use (`frontend/src/gameplay/racing.mjs`), or it means nothing.
- Infrastructure failures (runtime unreachable, harness crash) scored as a slow or
  bad lap.

## Constraints

- Runs go through `bench_run.py` + `BenchRunAdapter` unchanged; the case is just a
  new `benchmarks/<id>/` directory plus a small tool and grader.
- The runner image already has Node 20 and Python; the simulation is a pure ESM
  module with no dependencies. No new image.
- Validation runs with `network_mode="none"` (hardened lane), so grading must be
  offline: it replays a file, it never calls the game runtime.
- Determinism: same seed + same action list ⇒ same metrics, to the tick.

## Approaches considered

- **A. The agent plays through a CLI tool (chosen).** A `race` command in the
  workspace: `race state`, `race step '<controls json>'`. The agent's session is
  the game loop. Works with every harness unchanged and keeps the harness step
  meaningful. Cost: many tool calls per episode (one per decision), so slow and
  token-heavy.
- **B. The lab calls the model each step** (generalise the LM Studio / Codex
  policies into one policy per provider, new `runner_adapter`). Rejected: a CLI
  subscription start per step is ~4 min/episode for Codex, a per-provider
  integration to maintain, and "harness" has no meaning.
- **C. The agent writes a driver program** graded by running it. Rejected: it
  measures coding, not playing — a different question, closer to racing3d.

## Decisions taken with the user

1. Approach A.
2. A scripted driver is **allowed and labelled**, not forbidden: an agent may write
   a loop that calls `race step` with its own logic. The score counts either way;
   the run is labelled `played` or `scripted`, and comparison views show the label.
3. All configured models/harnesses, not only LM Studio.
4. Humans: deferred. This scope is models only.

## Design

### The tool (`race`)

- Shipped by the case into the workspace at provision time (a small Node script
  plus a shell shim on `PATH`, or invoked as `node race.mjs …` — builder's call).
- It holds the episode on disk (`<workspace>/.race/`), not in a long-lived process:
  each call loads the action log, replays it through `racing.mjs` to reconstruct
  state, applies the new action, appends it, and prints the new observation. A
  60-second episode is at most ~120 actions; replaying from scratch per call is
  cheap and removes any daemon/state-drift problem.
- Commands:
  - `race state` — current observation (same `observeRace` shape the existing
    LM Studio policy sees), countdown/terminal flags, elapsed race time.
  - `race step '{"throttle":..,"brake":..,"steer":..,"reset":..}'` — validates
    bounds (same as `Controls` in `gameplay/api.py`), advances one decision
    interval, prints the new observation and events (checkpoint, collision,
    finish). Refuses after terminal.
  - `race rules` — the fixed rules text (`RacingEnvironment.instructions`).
- Frozen condition (written into the case, recorded into the replay): seed 7,
  60 Hz, **30 ticks per action (0.5 s)**, 3 s non-interactive countdown,
  60 s racing horizon. Same values as the comparison Mauricio calibrated, where a
  scripted calibration lap finished in 22.0 s, so a valid lap is reachable.
- Every accepted `race step` call also appends a line to `.race/calls.jsonl` with
  a monotonic sequence number and wall timestamp — the evidence used for the
  played/scripted label.

### The prompt

`prompt_default.md`: the rules, the tool usage, the goal ("finish one valid lap as
fast as you can; the game time only advances when you call `race step`"), and an
explicit, neutral statement that writing a program that drives the car is
allowed and will be labelled as such. `prompt_harness.md` is generated as usual
(`make prompts`).

### Grading (validation_commands)

A Python grader in `benchmarks/racing-play-v1/grader/` (hidden from the agent via
`agent_hidden`, tamper-checked like racing3d's), run offline in validation:

1. Load `.race/actions.jsonl`, replay with Node through the shipped `racing.mjs`
   (the copy in `/app`, not the workspace copy — the agent cannot alter the engine
   that grades it), and compare against the recorded observations. A mismatch or a
   missing/corrupt log fails conclusively ("no valid episode").
2. Emit checks as the ladder (each a validation command, like racing3d):
   - `episode_valid` — a replayable episode exists with ≥1 action.
   - `progress_25`, `progress_50`, `progress_75` — fraction of the lap reached.
   - `all_gates` — every checkpoint in order.
   - `finished` — a valid lap within the horizon.
   - `clean` — finished with 0 collisions and 0 resets.
   - `fast` — finished under a par time set from calibration (builder sets it
     from measured runs; the scripted baseline's 22.0 s is the starting point).
   `test_pass_rate` therefore orders: nothing < some progress < lap < clean lap <
   fast lap, using the evaluator that already exists.
3. Write `metrics.json` with the raw numbers (finish time, progress, gates,
   collisions, resets, off-track seconds, actions, mean/max speed) and the
   `play_mode` label, so Results/Compare can show them beyond the ladder.

Infrastructure failures (Node missing, grader crash) are inconclusive, not zero,
per the existing ADR-0046 behaviour.

### Played vs scripted label

- `played`: the number of `race step` tool invocations visible in the harness
  event stream (`harness_events` already normalises tool_use across Claude, Codex,
  Gemini, OpenCode) is ≥ the number of actions in the episode, i.e. one model
  decision per action.
- `scripted`: fewer tool invocations than actions — a loop drove some steps.
- `unknown`: the harness emits no parsable tool events (show as such, never guess).
- Stored in the run's metrics and shown as a badge on the run, in Results and in
  Compare. Computed after the run from the event stream, not by the grader (the
  grader has no access to harness events in the hardened lane) — builder decides
  whether it lives in the evaluation step or a run post-processing hook.

### UI touch points

- Case card: category `gameplay`, tags e.g. `play · racing · deterministic`.
- Run detail/Results/Compare: show finish time / progress and the play-mode badge
  from `metrics.json`.
- Models flow: no change needed — harness levels and repetitions apply as is.

## Calls made after the builder check (author's calls, not user decisions)

1. **Engine visibility and hand-written logs.** The engine is not hidden: the agent
   can read `racing.mjs` (it is in the lab repo anyway) and can write
   `.race/actions.jsonl` directly. Neither is cheating under decision 2: a log with
   fewer recorded `race step` calls than actions is labelled `scripted`, and a log
   with no calls at all is the extreme case of that. A replay that does not match
   the engine is an invalid episode. An agent that runs its own look-ahead with the
   engine but still issues one `race step` per decision is `played`; the README
   says so explicitly.
2. **Label source.** Primary: tool_use events from `harness_events.translate()`
   (claude, codex, gemini, antigravity_cli). OpenCode has no tool-event parser
   today, so for OpenCode (and any harness without events) the label falls back to
   call timing in `.race/calls.jsonl`: steps issued less than 1 s apart in a run of
   ≥5 count as scripted. The badge names its source (`played · events` /
   `played · timing`) so a weaker signal is never presented as a strong one. No new
   OpenCode parser in this scope.
3. **Session ends mid-race** (run timeout, context or turn limit). The partial log
   is graded on the progress rungs — it is what the model did — and `metrics.json`
   records `episode_complete: false` with the cause. A run with no valid episode
   because the tool or Node failed is inconclusive (ADR-0046), never zero.
4. **60 s horizon.** `racing.mjs` is not changed (its 120 s `MAX_TICKS` and the
   existing page/replays stay as they are). The tool refuses steps past
   3 s + 60 s = 3780 ticks and reports `horizon` as terminal; the grader truncates
   the same way. A small wrapper module shared by tool and grader owns this rule.

## Open for the builder

- Shim mechanics for `race` on `PATH` across harnesses' shells.
- Exact par time for `fast` after a few calibration runs.
- Where the played/scripted computation runs.
- Per-run timeout for the case (an LM Studio small model took ~550–900 s for 120
  decisions in Mauricio's runs; frontier CLIs will differ). Set it generously and
  record the measured numbers in the case README.

## Risks

- Token cost: ~120 tool round trips per episode. Mitigation: 0.5 s decisions
  (not 0.2 s as on the existing page); document cost per run in the README.
- Some harnesses may batch or parallelise tool calls; the label counts calls, so
  parallel calls still count as played.
- Weak local models may never finish a lap (Mauricio: Gemma 7%, Qwen 3% progress).
  The progress rungs keep them measurable.

## Changes after the final review (2026-11-26)

- Each `.race/actions.jsonl` line records what the tool showed after that step
  (`seen`: tick, x, z, speed, heading); the grader rejects any line the replay does
  not reproduce. A log without `seen` is invalid.
- `played · events` additionally requires the tool's call log to have one entry per
  decision with no burst, and the logged controls to have been sent, in order, by
  recorded `race step` calls. A log rewritten after playing is `scripted`.
- The grader deletes and rewrites its report on every grade; the authoritative copy
  is `data/runs/<id>/racing.json` (final workspace only), the only one the evaluator
  reads, and only for `racing-play-v1`.
- Grader crash → exit 127, replay timeout → exit 124 (inconclusive).
- The metrics file is `.lab-grade/racing.json` / `data/runs/<id>/racing.json`, not
  `metrics.json`. Off-track seconds and speeds are in the report's `metrics` but not
  emitted as scores.
