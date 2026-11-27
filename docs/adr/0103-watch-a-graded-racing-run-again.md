# ADR-0103: Watch a graded racing run again on its run page

- **Status:** Accepted
- **Date:** 2026-11-27
- **Scope:** racing-play-v1 runs (ADR-0102); the run page
- **Guide:** [Tidal Ring gameplay](../racing-gameplay.md)

## Context

The first racing-play-v1 run (`racing-play-v1-K-001`, Claude Haiku 4.5) finished
a clean 15.72 s lap, but the only way to judge "did it play well?" was to read a
table of numbers. The Play tab (ADR-0078) starts a game an agent *built* in a
desktop sandbox; it has nothing to show for a game the agent *played*. The
standalone `/gameplay/racing` page can watch its own `GameplayEpisode` records,
at a different cadence (12 ticks per action), not a Run.

Everything needed is already in the run: the grader writes `racing.json` with the
frozen condition it replayed under and the controls it accepted.

## Decision

- `GET /api/runs/{id}/racing-replay` returns the condition and controls from the
  grader's `data/runs/<id>/racing.json` — never the agent-writable workspace copy
  — and only for a valid episode. The run context carries `racing_replay: bool`.
- The run page shows a **Replay** tab when that flag is set. It replays the
  controls with the lab's `racing.mjs`, one tick at a time, and renders with the
  existing Three.js scene, with play/pause, restart and 0.5×–4× speed.
- Tick-by-tick replay is equivalent to the grader's per-action `stepRace` call
  (the engine loops ticks inside it); `runReplay.test.mjs` pins that the watched
  lap ends with the graded metrics.
- The replay component is lazy-loaded so Three.js is not in every run page's bundle.

## Consequences

- Any racing-play-v1 run — played or scripted, any harness — can be watched after
  it is graded; a run graded before this change gets the tab on its next view.
- The replay is the browser re-simulating the controls, not a recording of
  frames. It is faithful as long as the engine the page ships matches the one
  the grader used; the tamper guard pins that engine to the commit.
- Human-versus-model ghosts and side-by-side comparison of two runs remain later work.
