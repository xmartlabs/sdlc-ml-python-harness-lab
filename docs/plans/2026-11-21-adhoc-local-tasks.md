# Plan — Ad hoc tasks on the operator's own repository (ADR-0095)

- **Date:** 2026-11-21
- **Branch:** `feature/adhoc-local-tasks`
- **Decision record:** [ADR-0095](../adr/0095-an-adhoc-task-runs-on-a-copy-and-is-applied-on-request.md)

## Goal

Use the lab for real development, not only benchmarks: pick a folder on this
machine, choose harness + model + effort, write a free prompt, run it in an
isolated copy, review a clean patch, and either **apply it to the real folder**
(button, all-or-nothing, with undo) or copy a command. Every run of a task is
measured (tokens, cost, time, decision) so the same task can be re-run with
another model and compared, while ad hoc runs keep the least data on disk.

## Design summary

| Concern | Decision |
|---|---|
| Reuse | An ad hoc task is a `TargetRepository` (lab copy under `data/checkouts/adhoc-<slug>`) plus one synthetic `Case` (`type=adhoc`, prompt = the task prompt). Runs go through the normal run pipeline (preflight, runner, progress, cost) with `harness_source=native`. |
| Base commit | Each task pins the operator's HEAD at task creation. Every run of the task starts from that commit — that is what makes "same task, other model" comparable. |
| Measurement | `AdhocRun` row per run: tokens, cost (real or estimated), minutes, turns, files/lines changed, decision, applied state. Survives every purge. |
| Clean patch | Runner writes `change.patch` for `source=adhoc` runs: `git diff --binary <pinned_commit>` after intent-to-add, with `.gitignore` honoured. Setup output and anything the lab staged before the baseline commit are excluded by diffing against the operator's commit with paths the lab created filtered out. |
| Apply | Worker task on the `interactive` queue launches a short-lived container from the worker image that bind-mounts **only** the operator's folder rw at `/repo`. Preconditions: no merge/rebase/cherry-pick in progress, no `index.lock`, no uncommitted change in a file the patch touches. Then `git apply --check` → `git apply`. Never commits, never touches the index or branches. |
| Undo | `git apply -R --check` then `git apply -R`, same container. Refused if the files changed since apply. |
| Concurrency | One apply/undo per folder at a time: a DB row (`AdhocRun.apply_state = applying`) plus a Redis `SET NX` lease keyed by the folder. No file locks (they do not work on the Docker Desktop bind). |
| Storage | Ad hoc run: after the run finishes, the archived `workspace/` is deleted immediately; `change.patch`, `usage.json`, `prompt.md`, logs stay until decision; on decision (reject/discard) or after `HARNESS_LAB_ADHOC_RETENTION_DAYS` (default 7) the run dir is removed and only DB rows remain. Applied runs keep `change.patch` (KB) so undo works, until purged. Benchmark runs: "light mode" deletes the archived `workspace/` (and `sandbox-*`) but keeps diff, logs, steps, usage; marked `purged_at`. |
| Separation | Ad hoc cases/runs are hidden from the case library, benchmarks, leaderboard, run lists, attention feed and experiments. |

## Write fence

The operator's folder must be under `HARNESS_LAB_LOCAL_SOURCES_DIR` (the folder
picker root). The worker translates the container path to the host path
(`HARNESS_LAB_HOST_LOCAL_SOURCES_ROOT`) and mounts only that folder. The API
keeps its `:ro` mount. The lab never writes inside `.git`.

## API contract (backend ↔ frontend)

All under `/api/adhoc`. JSON. Errors: 4xx with `{"detail": "..."}`.

- `GET /api/adhoc/source?path=<relative>` → inspect a folder under the local
  sources root: `{path, host_path, is_repo, branch, head, dirty, dirty_files: [str], remote_url}`.
- `POST /api/adhoc/tasks` body `{source: <relative path>, title?: str, prompt: str,
  setup_commands?: [str], validation_commands?: [str]}` → `AdhocTask`.
- `GET /api/adhoc/tasks` → `AdhocTaskSummary[]` (newest first).
- `GET /api/adhoc/tasks/{task_id}` → `AdhocTaskDetail` (task + runs + aggregates).
- `PATCH /api/adhoc/tasks/{task_id}` body `{title?, prompt?}` → `AdhocTask`
  (prompt edits allowed only while no run exists).
- `DELETE /api/adhoc/tasks/{task_id}` → 204 (refused while a run is live or a
  run is applied and not undone, unless `?force=true`).
- `POST /api/adhoc/tasks/{task_id}/runs` body `{harness, executor_model?,
  executor_effort?: str, max_steps?: int}` → `AdhocRunRecord` (queues it).
- `GET /api/adhoc/runs/{run_id}` → `AdhocRunRecord` (live state + metrics).
- `GET /api/adhoc/runs/{run_id}/patch` → `{patch: str, files: [{path, added,
  removed, status, binary, flags: [str]}], truncated: bool}`.
- `GET /api/adhoc/runs/{run_id}/apply-check` → `{can_apply, blockers: [str],
  warnings: [str], head_moved_by: int, overlapping_dirty: [str]}` (read-only).
- `POST /api/adhoc/runs/{run_id}/apply` → `AdhocRunRecord` (`apply_state:
  "applying"`, poll).
- `POST /api/adhoc/runs/{run_id}/undo` → `AdhocRunRecord` (`apply_state:
  "undoing"`, poll).
- `POST /api/adhoc/runs/{run_id}/decision` body `{decision: "approved" |
  "rejected" | "discarded", reason?: str, notes?: str}` → `AdhocRunRecord`.
- `GET /api/storage` → `{total_bytes, categories: [{key, label, bytes, items}],
  benchmark_light_candidates: {runs, bytes}, adhoc_expired: {runs, bytes}}`.
- `POST /api/storage/purge` body `{scope: "adhoc_expired" | "adhoc_all_files" |
  "benchmark_workspaces" | "sandboxes", older_than_days?: int}` →
  `{freed_bytes, runs: int}`.

`AdhocRunRecord` fields: `run_id, task_id, state` (run state), `harness,
executor_model, executor_effort, created_at, started_at, ended_at,
input_tokens, output_tokens, cache_read_tokens, reasoning_tokens,
billed_tokens, cost_usd, estimated_cost_usd, cost_source, billing_mode,
minutes, turns, tool_calls, files_changed, lines_added, lines_removed,
decision` (`pending|approved|rejected|discarded`), `decision_reason, notes,
apply_state` (`none|applying|applied|undoing|undone|failed`), `apply_error,
applied_at, files_purged` (bool), `failure_reason`.

## Phases (each independently verifiable)

1. **Backend core** (executor): models + migrations, `adhoc_service.py`
   (create task: checkout copy + pin + synthetic case; create run; metrics
   capture; decision), runner `source=adhoc` change.patch, hide ad hoc from
   listings. Verify: unit tests with real git repos in tmp.
2. **Apply / undo** (executor): `adhoc_apply.py` (pure git logic, testable on a
   tmp repo without docker) + Celery task launching the one-shot container +
   Redis lease. Verify: tests apply/undo/conflict/dirty-overlap on tmp repos.
3. **Storage** (executor): `storage_service.py` (usage by category, purge
   scopes, ad hoc TTL sweep at worker startup + after each ad hoc run). Verify:
   tests on a tmp `data/runs`.
4. **API** (executor): `adhoc_api.py` router. Verify: route tests.
5. **Frontend** (executor, parallel with 2–4 against the contract above):
   Ad hoc flow + task page + storage page, en/es. Verify: `make check-frontend`.
6. **Docs + ADR + memory** (fast): ADR-0095, `docs/guide-adhoc-tasks.md`,
   web UI guide entry, learnings.
7. **Gate**: `make check`, `make check-frontend`, code review.

## Risks / deferred

- Uncommitted operator changes are not included in the copy (warned in UI).
  Including them is deferred.
- Docker Desktop must share the local sources folder for the rw mount to work;
  the same requirement the read-only mount already has.
- The real end-to-end (a live model editing a real folder and the apply
  container writing to it) needs the stack running; covered by unit tests on the
  git logic plus a manual check documented in the guide.
