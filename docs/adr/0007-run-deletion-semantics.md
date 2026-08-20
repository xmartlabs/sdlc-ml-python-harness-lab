# ADR-0007: Run deletion semantics

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Marcos Soto (with Claude Code)
- **Related:** ADR-0002 (celery/docker-per-run; only the worker owns the docker
  socket), ADR-0006 (auto-incremental run ids),
  `src/harness_lab_app/run_service.py`, `src/harness_lab_app/api.py`,
  `src/harness_lab_app/tasks.py`, `src/harness_lab_app/models.py`

## Context

There was no way to delete a run. The models (`Run`, `RunEvent`, `Artifact`,
`Validation`, `Attribution`, `RunConfig`) have **no database-level foreign keys
or cascades** — they are joined by the `run_id` string convention only. A run
also leaves state outside the database: `data/runs/<run_id>/` (artifacts,
metadata, logs), `workspace/<run_id>/` (live working dir), and possibly a
leftover Docker container `harness-run-<safe-run_id>`. Deleting a run therefore
needs an explicit, ordered cleanup across three stores (DB, disk, Docker), and
must respect the process boundary: the API process has no docker socket.

## Decision

`RunService.delete_run(run_id, *, force=False)` performs a full, explicit
cleanup; the API exposes `DELETE /api/runs/{run_id}?force=false`.

- **Running-run guard.** Deleting a run in state `running` is refused with
  `RunDeleteConflictError` → HTTP 409 unless `force=True`. The intended flow is
  cancel first, then delete. `force` requests best-effort container teardown.
- **Explicit DB deletion order** (no cascades exist): `Validation`,
  `Attribution`, `Artifact`, `RunEvent`, then `Run`, then the run's `RunConfig`
  row (1:1 with the run today). All keyed by `run_id` except `RunConfig`
  (`run.run_config_id`).
- **Disk cleanup is API-safe and path-guarded.** Remove
  `settings.data_runs_dir / run_id` and `settings.workspace_dir / run_id`. Paths
  are built **only** from settings joined with the validated `run_id`; if the
  resolved path is not under `settings.repo_root`, the delete is refused. A
  missing directory is fine; a permission error surfaces rather than being
  swallowed.
- **Container teardown is worker-only.** Because the API cannot reach the docker
  socket (ADR-0002), `delete_run` enqueues a `cleanup_run_container(run_id)`
  Celery task that reuses the worker's `_stop_orphan_container` +
  `container_name_for_run`. If the broker is down, the leftover container is
  harmless once the run's rows and dirs are gone; the failure is logged, not
  fatal.

## Consequences

- Easier: runs can be removed from the UI (button in `RunDetail` + inline action
  in the run list), which also clears the stale `workspace/<id>/` that used to
  cause the RESUME trap.
- The three-store cleanup is not transactional across process boundaries: DB and
  disk are removed synchronously in the API; the container is removed
  asynchronously by the worker. This is acceptable because the container is
  disposable and idempotently reconciled at worker startup.
- The path guard (must resolve under `repo_root`) is the safety boundary that
  makes `shutil.rmtree` acceptable; `run_id` is never treated as a free-text
  path segment.
- Deletion is destructive and irreversible; the UI requires an explicit confirm.
