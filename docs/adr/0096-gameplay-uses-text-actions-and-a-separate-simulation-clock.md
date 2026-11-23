# ADR-0096: Gameplay uses text actions and a separate simulation clock

- **Status:** Accepted
- **Date:** 2026-11-23
- **Scope:** First contribution of the approved four-PR gameplay delivery
- **Guide:** [Text gameplay episodes](../gameplay-episodes.md)

## Context

The existing game cases measure models creating software. Evaluating models
playing an existing game is a different experiment: the game rules remain fixed,
and model actions determine outcomes. Including inference time in game physics
would confound planning/control ability with serving hardware and latency.
Human replay ghosts also require the original rules, scenario and recorded state.

## Decision

Introduce a language-independent, versioned JSON observation/action contract.
Observations describe visible state, instructions, bounded numerical controls and
a fixed next action interval. Actions reference the exact observation. A seeded
simulation advances only after a valid action; model latency is measured separately.
Invalid actions stop the episode without silent repairs. Provider failures and
timeouts are inconclusive rather than zero-valued gameplay outcomes.

Record an incremental journal plus an atomically finalized replay containing
config, controller provenance, actions, events and observation snapshots. Verify
replays by re-running the original environment version. Verification establishes
internal consistency, not authenticity or the truth of latency/provider claims.

PR1 proves the instrument with a small Python fixture and a direct LM Studio
adapter. It does not change existing benchmark execution or introduce a browser
runtime. Subsequent PRs add a fixed racing game, human ghost challenges, then a
fixed cooking game. A TypeScript/browser simulation can implement the same wire
contract; no Python simulation implementation is prescribed for those games.
Text observations are the first track; vision is deferred.

## Consequences

- Slow and fast model servers can be compared on simulated gameplay and latency
  as distinct quantities. This first mode does not measure real-time reactions.
- Comparable episodes require shared seeds, environment versions and action cadence.
- Future games must retain their simulation version and define visible state,
  exact event timing, numerical metrics and retention for selected replays.
- The fixture stays outside benchmark leaderboards and does not imply an existing
  database, browser, game or ghost integration.
