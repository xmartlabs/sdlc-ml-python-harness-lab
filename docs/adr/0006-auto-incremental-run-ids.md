# ADR-0006: Auto-incremental run IDs for the web lane

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Marcos Soto (with Claude Code)
- **Related:** ADR-0002 (celery/docker-per-run), ADR-0003 (provenance/condition
  hash), `src/harness_lab_app/run_service.py`, `src/harness_lab_app/schemas.py`,
  `scripts/bench_run.py`, `scripts/create_run.py`

## Context

Creating a run from the web app required a user-supplied, globally unique
`run_id` (`RunCreate.run_id: str`). Duplicates returned HTTP 409, and there was
no generator. To queue several runs the user had to invent a fresh unique name
each time, and a stale `workspace/<run_id>/` from a prior failed attempt made a
brand-new run with the same id fail at `validate_workspace()` (the "set
RESUME=1" trap). This made rapid, systematic experimentation impractical.

A second, latent blocker: `bench_run.main()` resolves the run through
`find_run()` (`create_run.py`), which `sys.exit`s when the `run_id` is absent
from `benchmarks/<case>/runs.yaml`. That YAML only defines manual CLI cells
(A–J / 001–003). Any generated web id would not be in the YAML and would die at
resolution — so id generation and run resolution had to be solved together.

## Decision

The server assigns run IDs for the web lane, and the runner tolerates ids that
are not registered in `runs.yaml`.

- **`K` is the reserved web lane.** Generated ids follow `<case_id>-K-NNN`
  (zero-padded, e.g. `tetris-v1-K-001`). `K` keeps web runs from colliding with
  the manual CLI cells (A–J) and matches the pre-existing frontend hint
  `${caseId}-K-001`.
- **`run_id` becomes optional** in `RunCreate`, plus an optional `label`
  override. When omitted, `RunService.create_run` calls `_next_run_id(case_id)`,
  which scans existing `Run.run_id` matching `^<case>-K-(\d+)$`, takes
  `max(NNN)+1`, and **also skips any candidate whose `data/runs/<id>` or
  `workspace/<id>` already exists on disk** — so generated ids are unique across
  both the database and the filesystem. This is what guarantees each run a clean,
  private workspace and sidesteps the RESUME trap by construction.
- **Explicit ids keep the 409 contract.** A user-supplied `run_id` or `label`
  is validated for uniqueness exactly as before (HTTP 409 on duplicate).
- **The runner synthesizes an unregistered run** instead of exiting.
  `bench_run.py` gains `resolve_or_synthesize_run()`: if the id is present in
  `runs.yaml` its behavior is unchanged; otherwise it builds the run dict from
  the CLI args (`prompt_variant`, `executor_model`/`planner_model`,
  `langfuse_session_id` defaulting to `bench-<run_id>`). The API never writes to
  the shared `runs.yaml` (avoiding write races against the CLI); YAML
  registration stays a CLI-only convenience.

## Consequences

- Easier: users leave the id blank and keep queuing runs; "Run again" clones a
  prior config under the next free id. The stale-workspace failure mode
  disappears for web runs because ids never collide.
- The DB is the source of truth for run identity; `runs.yaml` is legacy CLI
  state the runner still reads but no longer requires.
- The `K` convention is a naming policy, not a schema constraint — non-web
  callers may still pass any explicit id. Provenance/`condition_hash` (ADR-0003)
  is unaffected: the id is not part of the arm identity.
- Uniqueness now depends on both DB rows and on-disk dirs; a purge that removes
  one but not the other (see `reset_db.py --purge-disk`) keeps generation
  correct because the generator checks both.
