# Text gameplay episodes: contract and recording

This is the first of four approved contributions: text episode contract and
recorder; fixed playable racing; human-versus-recorded-model ghosts; fixed
playable cooking. Vision is later. Existing cases that ask models to **build**
games are unchanged. This contribution contains a small instrument fixture, not
a racing/cooking benchmark, a browser game, or a ghost renderer.

## Run the fixture

After the normal development environment setup:

```bash
make gameplay-episode EPISODE=fixture-demo SEED=7
make gameplay-verify REPLAY=data/runs/fixture-demo/gameplay/replay.json
```

Each recording needs a new output directory. The runner refuses to overwrite an
existing one. Seed 7 sets a target four cells to the right. The scripted controller
moves right twice, two ticks per decision. At ten ticks per second the result is
four simulation ticks (0.4 seconds), with `reached_goal=1` and
`distance_remaining=0`. These are hand-checkable instrument outcomes, not model
capability estimates.

For an **already loaded** LM Studio model, use its exact inventory key:

```bash
make gameplay-episode EPISODE=fixture-local MODEL=google/gemma-4-e4b
# A different already-running server:
make gameplay-episode EPISODE=fixture-remote MODEL=qwen/qwen3.5-9b BASE_URL=http://host:1234/v1
```

The adapter uses the existing native model discovery and completion boundary,
checks that an instance is loaded, and addresses that instance directly. It never
loads/unloads/downloads models or falls back to the gateway or a coding CLI.
Inventory and inference happen separately: if the chosen instance disappears,
the episode records the provider failure; the adapter does not manage its lifecycle.
CLI flags also allow a seed, maximum tick budget, output directory and decision
timeout (`python scripts/gameplay_episode.py --help` with `PYTHONPATH=src`).
The CLI exits nonzero for invalid actions, timeouts and provider failures.
A valid episode reaching its time budget is a result, not an infrastructure error.

## Wire contract

`src/harness_lab_app/gameplay/contracts.py` defines Pydantic models, each tagged
`gameplay.v1`. Their `model_json_schema()` output is the language-independent
JSON interface; the eventual browser simulation does not need Python physics.
An absent version defaults to v1; an explicit unsupported version is refused.
Unknown fields, coerced strings/booleans as numbers, NaN and infinity in controls
are refused. Environment state must be JSON-serializable with finite numbers.

An observation contains:

- Episode ID, simulation tick, tick rate and the exact number of ticks the next
  accepted action advances (clamped to the episode's remaining budget).
- Instructions and player-visible state. The fixture exposes position and goal;
  future games must define their own visible state without grader/private data.
- Named numerical controls and their inclusive bounds.
- A terminal flag and observation ID: SHA-256 over the canonical observation,
  config and environment identity/version.

Example action:

```json
{"version":"gameplay.v1","observation_id":"<exact observed id>","controls":{"move":1.0}}
```

The controller must supply **exactly** the advertised controls and reference the
current observation. It cannot choose a time interval, teleport, change game
state, edit files or call game internals. Values are validated before stepping.
The fixture interprets the sign of `move` as one cell per tick. Each environment
owns the meaning of its controls; the runner owns action cadence and the budget.

The environment boundary is deliberately small: reset by seed, visible state,
control bounds, step by fixed ticks, terminal state and numerical metrics. The
controller boundary receives one observation plus an I/O timeout and returns a
JSON action with optional provider facts. Controllers receive a copy of the
observation, so edits cannot change the authoritative state.

## Clocks and outcomes

Simulation advances only after an accepted action. It stays paused during model
inference, parsing, recording and verification. Controls are held for the full
interval; terminal simulations freeze for any remaining ticks in that interval.
The fixture therefore measures completion at decision boundaries. A future game
needing sub-interval finish times must report its own tick-stamped finish metric.

A monotonic clock measures the whole controller call, including provider/network
latency. The result records total inference seconds, median latency and p95
(nearest rank). These are separate from `simulation_ticks / tick_rate`.
They exclude environment setup and disk writes and are not token-generation-only
speed measurements. LM Studio requests are capped at 512 output tokens per
observation; a response truncated before valid JSON is an invalid action, never
silently repaired. Thinking models may need a separately versioned budget later.

Invalid JSON, stale IDs, missing/extra controls and out-of-range controls terminate
as `invalid_action`, without advancing simulation. A timed-out or late decision is
not applied. Timeouts and provider errors have no gameplay metrics, so absence of
measurement does not become a zero capability score. Failure frames record the
outcome and latency; model replies are retained when available. Raw provider
exceptions are not persisted because they may include credentials or private
response bodies. There are no automatic retries or hidden corrective prompts.

Adapters must enforce the supplied I/O timeout. The runner also rejects late
returns; it cannot forcibly interrupt an arbitrary Python callback that ignores
its timeout. Interrupts, environment failures and disk errors propagate rather
than becoming model failures; their partial journal remains available.

## Recording and verification

The episode directory contains:

- `journal.jsonl`: flushed and fsynced header (config, environment version,
  initial snapshot and controller identity), each decision, then the result.
  An interrupted run remains incomplete; it must not be ranked as a completed run.
- `replay.json`: atomically published after the episode, containing every accepted
  action, response when available, event, observation snapshot/ID, measured latency,
  provider facts and final numerical result. Publication failures clean temporary
  files while preserving the journal.

Verification reinitializes the exact environment version and seed, reapplies each
accepted action, and recomputes intervals, snapshots, observation IDs, events,
terminal/budget boundaries, latency aggregates and game metrics. It rejects
missing frames, post-terminal frames and inconsistent outcomes. It does **not**
prove latency or provider claims, authenticate a recording, or detect an attacker
rewriting an entire self-consistent episode. Exact environment versions must be
retained for future deterministic replay; snapshots will also support ghost
rendering independently of resimulation.

Replay files live outside `workspace/` and `sandbox-*`, matching the existing
`ArtifactService` indexing boundary. These standalone fixture episodes do not
create a `Run`, register a `Case`, enter a leaderboard or write database `Score`
rows. The racing integration can attach episodes to real runs, index their
artifacts, and map numerical metrics to the existing name/version/provenance
score representation. The fixture's versioned results avoid pretending those
integrations exist already. Recording retention must be addressed when ghosts
become selectable so workspace cleanup cannot delete a chosen ghost.

## Validation scope

Automated tests cover hand-computed simulation/latency outcomes, seed repeatability,
action validation, exact budget boundaries, corrupt/truncated replays, exclusive
recordings and failed publication. Local fake-HTTP integration tests exercise the
existing LM Studio adapter, instance selection, usage capture, unloaded-model
refusal and real socket timeouts. A subprocess test exercises CLI run and verify.
They make no inference calls and do not depend on the developer's credentials.

The live smoke tests use only the three installed models requested by the user:
`google/gemma-4-e4b`, `qwen/qwen3.5-9b` and `prism-ml/bonsai-27b`. Their actual
outcomes are reported separately from deterministic tests. Neither these tiny
fixture attempts nor successful replay verification establish racing, cooking,
vision, or human-versus-model performance.
