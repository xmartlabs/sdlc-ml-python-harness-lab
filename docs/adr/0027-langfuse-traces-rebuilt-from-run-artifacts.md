# ADR-0027: Langfuse traces are rebuilt from run artifacts, not emitted live

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Marcos Soto
- **Related:** [ADR-0002](0002-celery-docker-per-run-execution.md),
  [ADR-0021](0021-benchmark-artifact-integrity.md),
  [ADR-0018](0018-health-and-drift-panel.md),
  [guide-langfuse-observability.md](../guide-langfuse-observability.md),
  [plans/2026-08-langfuse-observability.md](../plans/2026-08-langfuse-observability.md)

## Context

Inspecting a single test case meant opening `data/runs/<id>/` and reading
`steps/step-N.log` by hand. Nothing lets you compare two arms turn by turn, see
which step burned the tokens, or share a link to "the run where the model looped
on the same edit". The lab measures agents and cannot look inside one.

Langfuse is the obvious destination — the lab already has `LANGFUSE_HOST` and a
key pair in `.env` for the gateway. The question was where the traces come from.

The tempting answer is to emit them live from `scripts/bench_run.py` as each
step runs. That fails three ways here:

1. **A run is one Docker container (ADR-0002)** that can be killed by the
   watchdog, by cancel, or by the 7200s timeout. Live emission loses exactly the
   runs an operator most wants to inspect — the ones that died.
2. **113 runs already exist on disk.** A live emitter can never show them.
3. **`Dockerfile.runner` would need the `langfuse` SDK**, and the SDK's
   `start_observation()` takes no `start_time`/`end_time`: it stamps spans from
   the live OTEL clock, so it cannot express when a step *actually* ran.

Meanwhile every fact a trace needs is already written down and guaranteed to
survive (ADR-0021): `metadata.json`, `prompt.md`, `steps/step-N-prompt.md`,
`steps/step-N.log` with in-log timestamps, `usage.json`, `diff.patch`,
`report.md`. The database adds state, exit code, score and validation.

## Decision

**Traces are built by reading a finished run's directory, and posted as
OTLP/JSON over plain `urllib`.**

- **One Langfuse session per run**, id `bench-<run_id>` — the id
  `bench_run.resolve_or_synthesize_run` has already been writing into
  `metadata.json` since long before anything read it back. A single-prompt case
  and a ten-step case use the same shape: the session holds one trace per step
  plus a `run:<id>` envelope trace.
- **Ids are deterministic** (`sha256(run_id:key)`), so re-exporting a run
  updates its session instead of duplicating it. Backfill is therefore safe to
  re-run, and the worker's automatic export and a later manual export converge.
- **OTLP over HTTP, hand-built.** Langfuse v4 rejects `/api/public/ingestion`
  for spans (400) and never had `/api/public/traces` (404); OTLP is the
  supported route and accepts JSON as well as protobuf. Building the payload by
  hand keeps `Dockerfile.runner` unchanged and lets a future live emitter import
  the same module from inside a container.
- **Off is the default and it is silent.** Unless `LANGFUSE_ENABLED=true` *and*
  both keys are present, the config resolves to disabled, the transport is a
  no-op, and no socket is opened. The health row reports "off" as `ok`, not as a
  warning — most labs never turn this on.
- **Fail open, everywhere.** The exporter never raises; the worker hook records
  a run event and moves on. A Langfuse outage costs observability and nothing
  else.
- **Credentials are masked before anything leaves the machine**, reusing the
  harness sanitizer. Home paths and loopback addresses are deliberately *not*
  masked: they are what makes a trace useful.

## Consequences

- Every run that has ever finished is exportable — `make langfuse-backfill`
  replays all of them, including the failed ones.
- Traces appear at the end of a run, not during it. Watching a run live is still
  the run detail page's job.
- Token usage is per run (`usage.json`), not per model call. Per-generation
  cost, prompts and completions require the gateway to forward them under the
  same `session_id`; that is Layer B and is not built yet.
- Step timing for harnesses that log no clock (claude) is inferred from the step
  header plus file mtimes, clamped so a step can never appear to end after the
  next one started. It is approximate by construction.
