# Roadmap: Local Web App for Agentic Harness Lab

## Purpose

Build a local web app for configuring, running, inspecting, scoring, and auditing
agentic benchmark runs. The app must start by wrapping the existing CLI workflow
instead of replacing it.

Tetris is the pilot case. The product model must support new benchmark cases from
the beginning: single-prompt tasks, multi-step tasks, repo tasks, bugfix tasks, and
future SWE-bench-style instances.

## Progress Snapshot

- [x] Docker-first runtime and local FastAPI/React scaffold are in place.
- [x] DB-backed case model, Tetris seeding, CRUD, clone, delete, and YAML sync are in place.
- [x] Case creation and run creation are now available from the web UI.
- [x] CLI compatibility remains intact and the repo still passes `make check`.
- [x] Worker streams process stdout/stderr into `RunEvent` incrementally while
      the run is still active, instead of only after it exits.
- [x] Run detail view polls logs while `running` and renders them as a
      chronological console, plus exit code, started/ended timestamps.
- [x] Execution backend replaced by Celery + Redis + one Docker container per
      run (SWE-bench style) — see `docs/adr/0002-celery-docker-per-run-execution.md`.
      The `Dockerfile.runner` image bundles opencode + node + uv; runner
      containers reach the host LiteLLM/LM Studio via `host.docker.internal`.

## Guiding Decisions

- [x] Keep `scripts/bench_run.py` and `make bench-*` as the first execution backend
      (`BenchRunAdapter`/`tasks.py` always run `bench_run.py` — now inside a
      per-run Docker container; `make bench-*` targets are untouched and still
      pass `make check`).
- [x] Keep `benchmarks/*/task.yaml`, `runs.yaml`, `results.csv`, and `data/runs/`
      compatible with the current CLI workflow (the app imports/syncs
      `task.yaml` and reads `data/runs/`, but never writes `runs.yaml` or
      `results.csv`, so the CLI's own bookkeeping stays untouched).
- [x] Use the database as the web app's operational state.
- [x] Use the filesystem as the artifact store for the MVP.
- [x] Treat case management as a primary product area, not a hidden settings page.
- [x] Run a UI/product brainstorming pass before implementing frontend screens.
- [x] Design for local, single-user operation first, without blocking future
      multi-user use.

## Phase 0: Current Contract and Sources of Truth

Goal: document the current harness contract so the web app wraps the real system.

- [x] Inventory benchmark definitions in `benchmarks/*/task.yaml` (six cases:
      `tetris`, `snake`, `fastapi-endpoint`, `bugfix-with-tests`,
      `nemotron-all-120b`, `nemotron-planner-qwen-executor`).
- [x] Inventory run registries in `benchmarks/*/runs.yaml`.
- [x] Inventory scored result tables in `benchmarks/*/results.csv`
      (`benchmarks/tetris/results.csv` is the only populated one so far).
- [x] Inventory run artifacts in `data/runs/<run_id>/`.
- [x] Inventory active workspaces in `workspace/<run_id>/`.
- [x] Document `scripts/bench_run.py` CLI arguments and behavior (see
      `docs/runbook-first-run.md` and `BenchRunAdapter.build_command` in
      `src/harness_lab_app/runner_adapter.py`, which mirrors the same flags).
- [x] Document `make bench-run`, `make bench-results`, `make bench-start`,
      `make bench-close`, `make bench-audit`, `make bench-agents-audit`, and
      `make bench-reset` (documented in `docs/runbook-first-run.md` and the
      `Makefile` target comments).
- [x] Define which state the web app reads from YAML/CSV/filesystem: cases
      are imported from `task.yaml` (`CaseService.import_task_yaml`) and can be
      synced back out (`CaseService.sync_case_to_yaml`); `runs.yaml`/
      `results.csv` remain the CLI's own state and are not read by the app yet.
- [x] Define which state the web app writes to DB only: `Case`, `RunConfig`,
      `Run`, and `RunEvent` are DB-only; the app never writes `runs.yaml` or
      `results.csv` directly.
- [x] Define which actions still call the existing CLI: run execution always
      shells out to `uv run python scripts/bench_run.py ...` via
      `BenchRunAdapter`/`worker.py` — the app never re-implements the harness
      itself.
- [x] Confirm Tetris remains a pilot, not a special-case domain model (the
      `Case`/`RunConfig` schema is generic; Tetris is just the seeded case).

Acceptance criteria:

- [x] A developer can explain where cases, runs, scores, logs, diffs, usage, and
      audits live today (see `docs/runbook-first-run.md` for the CLI side and
      this section for the web app side).
- [x] The app contract says when DB state and CLI artifacts must stay in sync
      (see the "Define which state..." bullets above).
- [x] No existing CLI command is removed or made obsolete by the roadmap
      (`make bench-*` targets are unchanged and still work standalone).

## Phase 1: Product and UI Design with Brainstorming

Goal: design the user experience before building UI.

- [x] Run the `brainstorming` skill before frontend implementation starts.
- [x] Produce a UI design spec and save it before coding the frontend.
- [x] Define the main navigation structure.
- [x] Design the dashboard view.
- [x] Design the case management view.
- [x] Design the create/edit case flow.
- [x] Design the new run flow.
- [x] Design the run detail view.
- [x] Design logs, artifacts, diff, score, and audit panels.
- [x] Design the comparison view.
- [x] Define empty states for no cases, no runs, no artifacts, no logs, and no
      audits.
- [x] Define loading, running, failed, cancelled, and completed states.
- [x] Define how long forms handle prompt bodies, rubrics, constraints, validation
      commands, and metadata.
- [x] Define visual style, density, typography, color use, table behavior, and
      panel layout.

Design defaults:

- Use a workbench-style product UI rather than a marketing-style landing page.
- Make case management a top-level nav item.
- Favor dense, scannable tables for repeated operational work.
- Keep logs and diffs readable before adding decorative polish.
- Use clear state badges for run lifecycle state.

Acceptance criteria:

- [x] The design spec covers all primary screens and states (see
      `docs/superpowers/specs/2026-07-07-local-webapp-design.md`).
- [x] The design supports both single-prompt and multi-step cases (spec's
      "Case Authoring" section covers both explicitly).
- [x] The design makes run setup fast for repeated benchmark work (template-first
      wizard + `New Run` flow in the spec).
- [x] The design can be implemented without inventing screens during coding
      (the built frontend's `Dashboard`, `Cases`, `Runs`, `RunDetail`, `Compare`,
      `Settings` pages match the spec's "Screens" section 1:1).

## Phase 2: Persistent Case Model

Goal: let users create, edit, delete, clone, and persist benchmark cases in DB.

- [x] Add a persistent `cases` model.
- [x] Add persistent prompt variants for each case.
- [x] Add persistent case validation settings.
- [x] Add persistent case rubric settings.
- [x] Add persistent case constraints.
- [x] Add persistent case metadata.
- [x] Add create, read, update, delete, clone, and deactivate operations.
- [x] Seed Tetris from `benchmarks/tetris/task.yaml`.
- [x] Support export or sync to `benchmarks/<case>/task.yaml`.
- [x] Track whether a case was imported, created in UI, cloned, or synced from YAML.

Case types (all modeled in `CaseType`, `src/harness_lab_app/models.py`):

- [x] `single_prompt`
- [x] `multi_step`
- [x] `build_from_scratch`
- [x] `bugfix`
- [x] `repo_task`
- [ ] `swe_bench_instance` (modeled in the enum, deferred to Phase 6 — no
      dedicated fields or UI template yet)

Minimum case fields (all present on the `Case` model,
`src/harness_lab_app/models.py`):

- [x] Case ID.
- [x] Name.
- [x] Description.
- [x] Type.
- [x] Status.
- [x] Tags.
- [x] Subject repository.
- [x] Base commit.
- [x] Prompt variants.
- [x] Step prompts for multi-step cases.
- [x] Success criteria.
- [x] Scoring rubric.
- [x] Constraints.
- [x] Validation commands.
- [x] Timeout.
- [x] Max steps.
- [x] Allowed files.
- [x] Metadata.

Acceptance criteria:

- [x] A user can create a single-prompt case from the UI.
- [x] A user can create a multi-step case from the UI.
- [ ] A user can edit a case without editing YAML by hand (`PATCH` endpoint
      exists; no edit form in the frontend yet).
- [ ] A user can delete or deactivate a case safely (endpoints exist; no
      delete/deactivate button in the frontend yet).
- [ ] A user can clone Tetris into a new editable case (`clone` endpoint
      exists; no clone button in the frontend yet).
- [x] The web app can still read the existing Tetris case.

## Phase 3: Minimal Web App over the CLI

Goal: ship the first usable local app without rebuilding the runner.

Backend:

- [x] Add a local FastAPI app.
- [x] Add simple local auth.
- [x] Add case endpoints.
- [x] Add run endpoints.
- [x] Add artifact endpoints.
- [x] Add log endpoints.
- [x] Add score endpoints.
- [ ] Add audit endpoints.
- [x] Add health and configuration endpoints.
- [x] Execute runs by calling `uv run python scripts/bench_run.py` or
      `make bench-run`.

Frontend:

- [x] Add a React/Vite frontend.
- [ ] Add login (backend `POST /api/auth/login`/`logout` exist; no login page
      or session gating in the frontend yet).
- [x] Add dashboard.
- [x] Add case management.
- [ ] Add case create/edit/delete/clone flows (create is wired in `Cases.tsx`;
      edit/delete/clone endpoints exist in `CaseService`/`api.py` but have no
      frontend buttons yet).
- [x] Add new run flow.
- [x] Add run list.
- [x] Add run detail.
- [ ] Add artifacts view (run detail only shows a static filename hint, not an
      indexed/downloadable artifacts list — see Phase 5).
- [x] Add logs view.
- [ ] Add diff view.
- [x] Add score panel (frontend form in `RunDetail.tsx` now submits to
      `POST /api/runs/{run_id}/score` for `awaiting_manual_score` runs).
- [ ] Add audit panel.

Initial API surface (all implemented in `src/harness_lab_app/api.py`):

- [x] `POST /api/auth/login`
- [x] `POST /api/auth/logout`
- [x] `GET /api/cases`
- [x] `POST /api/cases`
- [x] `GET /api/cases/{case_id}`
- [x] `PATCH /api/cases/{case_id}`
- [x] `DELETE /api/cases/{case_id}`
- [x] `POST /api/cases/{case_id}/clone`
- [x] `POST /api/cases/{case_id}/sync`
- [x] `GET /api/runs`
- [x] `POST /api/runs`
- [x] `GET /api/runs/{run_id}`
- [ ] `POST /api/runs/{run_id}/start` (no dedicated route — a `pending` run
      is picked up automatically by the worker's poll loop after
      `POST /api/runs`, so an explicit start endpoint was never needed).
- [x] `POST /api/runs/{run_id}/cancel`
- [x] `POST /api/runs/{run_id}/retry`
- [x] `POST /api/runs/{run_id}/score`
- [ ] `POST /api/runs/{run_id}/audit` (not implemented yet — Phase 5).
- [x] `GET /api/runs/{run_id}/logs`
- [x] `GET /api/runs/{run_id}/artifacts`
- [x] `GET /api/runs/{run_id}/artifacts/{artifact_name}`

Acceptance criteria:

- [ ] A user can log in locally (no frontend login flow yet).
- [x] A user can create a case.
- [x] A user can select a case and configure a dry run.
- [x] A user can start a Tetris run through the web app.
- [x] A user can inspect the generated run record.
- [x] The CLI workflow still works after using the web app (`make bench-*`
      targets are untouched; verified by `make check`).

## Phase 4: Queue, Lifecycle States, and Live Logs

Goal: run benchmarks without blocking the web server.

- [x] Add a local run queue (Celery + Redis broker; `celery_app.py`, queue
      `runs`, worker concurrency 1).
- [x] Persist queue jobs in DB (the `run` table is the single source of truth;
      Celery uses `task_ignore_result` — no result backend).
- [x] Add a worker process (`celery -A harness_lab_app.celery_app worker`,
      its own compose service with the docker socket mounted).
- [x] Execute each run in its own Docker container (`Dockerfile.runner` image
      with opencode + conductor plugin; `DockerRunExecutor` launches sibling
      containers via the host daemon and bind-mounts the host repo rw).
- [x] Stream run output to run events (container logs → `RunEvent`, incremental).
- [ ] Tail `data/runs/<run_id>/steps/*.log` when available.
- [x] Expose live logs through polling (`GET /api/runs/{run_id}/logs?ascending=true`).
      SSE/WebSocket deferred — polling is sufficient for a single local user.
- [x] Support cancellation when the process is still running (DB `cancel_requested`
      flag polled by the task, which then stops the run container).
- [x] Support retry for failed runs (frontend action wired to `POST
      /api/runs/{run_id}/retry`; re-publishes the Celery task).
- [x] Support resume when `bench_run.py --resume` applies (retry persists
      `resume=True` on the config; the task rebuilds the command from config).
- [x] Record run start time, end time, duration, exit code, and failure reason.
- [x] Reconcile orphaned `running` runs on worker startup (stop leftover
      containers, mark `failed`, suggest retry/resume).
- [x] Enforce a run timeout inside the task (`HARNESS_LAB_RUN_TIMEOUT_SECONDS`,
      default 7200) with a clean container stop.

Run states (all declared in `RunState`, `src/harness_lab_app/models.py`; checked
items are also reachable through an actual `RunService` transition today):

- [ ] `draft` (declared, no creation path writes it yet — runs start at `pending`)
- [x] `pending`
- [x] `running`
- [x] `awaiting_manual_score`
- [ ] `scoring` (declared, `score_run` jumps straight to `completed` without it)
- [ ] `auditing` (declared, no audit endpoint/transition yet — Phase 5)
- [x] `completed`
- [x] `failed`
- [x] `cancelled`

Acceptance criteria:

- [x] Starting a run returns immediately.
- [x] The run continues in a worker.
- [x] Logs appear while the process is active.
- [x] Failed runs show useful failure details.
- [x] Cancel, retry, and resume are visible only when valid for the current state.

## Phase 5: Artifacts, Diff, Score, and Audit

Goal: make each run inspectable from the browser.

Decision: `score_mode=interactive` is unsupported for web-created runs because
the worker process has no stdin channel for `input()`. The web flow standardizes
on `score_mode=manual` plus `POST /api/runs/{run_id}/score` after completion.

- [x] Index run artifacts from `data/runs/<run_id>/` (`ArtifactService.index_run`
      + `POST /api/runs/{run_id}/artifacts/index`; not yet triggered from the
      frontend or shown as a list — see the unchecked "Show"/"download" items
      below).
- [ ] Show `metadata.json`.
- [ ] Show `prompt.md`.
- [ ] Show `usage.json`.
- [ ] Show `diff.patch`.
- [ ] Show `report.md`.
- [ ] Show `audit.md`.
- [ ] Show step logs from `steps/step-*.log`.
- [ ] Add artifact download links.
- [x] Add manual score entry.
- [x] Add human intervention entry.
- [x] Add notes entry.
- [ ] Close scored runs through the same contract as `bench-close`.
- [ ] Launch descriptive run audit through the same contract as `bench-audit`.
- [ ] Show audit status and final audit content.

Acceptance criteria:

- [ ] A user can inspect all standard artifacts for `tetris-v1-J-002`.
- [x] A user can score a run from the UI.
- [ ] A scored run updates the same compatibility outputs as the CLI flow.
- [ ] A user can launch and read an audit from the UI.
- [ ] Missing artifacts produce clear empty states, not broken panels.

## Phase 6: SWE-bench Readiness

Goal: prepare the domain model for stricter external benchmarks.

- [ ] Add `swe_bench_instance` as a case type.
- [ ] Model upstream benchmark ID.
- [ ] Model repo URL.
- [ ] Model base commit.
- [ ] Model issue prompt.
- [ ] Model expected patch metadata when available.
- [ ] Model test commands.
- [ ] Model evaluation commands.
- [ ] Model environment setup notes.
- [ ] Model timeout and resource limits.
- [ ] Separate manual scoring from automated evaluation.
- [ ] Document differences between Tetris-style scoring and SWE-bench-style
      evaluation.
- [ ] Defer full SWE-bench import and execution until after the MVP.

Acceptance criteria:

- [ ] The DB model can represent a SWE-bench-style case.
- [ ] The UI can display a SWE-bench-style case without custom code.
- [ ] The roadmap does not require implementing SWE-bench execution in the MVP.
- [ ] Future SWE-bench import can be added without replacing the case model.

## Phase 7: Comparison and Metrics

Goal: compare runs across case, prompt, harness, and model dimensions.

- [ ] Add dashboard totals for runs, success rate, average score, total duration,
      total tokens, and total cost.
- [ ] Add model effectiveness summaries.
- [ ] Add prompt variant summaries.
- [ ] Add harness summaries.
- [ ] Add cost vs score view.
- [ ] Add duration vs score view.
- [ ] Add interventions vs score view.
- [ ] Add model x harness x prompt matrix.
- [ ] Add side-by-side run comparison.
- [ ] Add diff comparison.
- [ ] Add artifact comparison.
- [ ] Add audit comparison.

Acceptance criteria:

- [ ] A user can compare Tetris runs by model, prompt, and harness.
- [ ] A user can see cost, tokens, duration, score, and interventions together.
- [ ] A user can compare two runs without opening files manually.
- [ ] Missing usage data is clearly marked as unavailable.

## Suggested Technical Defaults

- [x] Backend: FastAPI (`src/harness_lab_app/api.py`).
- [x] Frontend: React with Vite (`frontend/`).
- [x] MVP database: SQLite (`config.py` default `sqlite:///data/app/harness_lab.db`).
- [ ] Later database: PostgreSQL (not needed yet — single local user).
- [x] Queue: Celery and Redis (broker only), one Docker container per run —
      replaced the initial DB-polling `worker.py` (removed). See
      `docs/adr/0002-celery-docker-per-run-execution.md`.
- [x] Auth: local username and password (`POST /api/auth/login` checks
      `settings.local_username`/`local_password`; not yet gated in the
      frontend — see Phase 3).
- [x] Artifact storage: filesystem under `data/runs` (`ArtifactService`
      indexes `data_runs_dir`).
- [ ] Tracking integrations: consume Langfuse, LiteLLM, and MLflow outputs
      when present (not implemented yet).

## Validation Plan

- [x] Run API unit tests for case CRUD (`tests/harness_lab_app/test_cases.py`).
- [x] Run API unit tests for run creation (`tests/harness_lab_app/test_runs.py`).
- [x] Run API tests for artifact indexing (`tests/harness_lab_app/test_artifacts.py`).
- [x] Run worker tests for valid lifecycle transitions (`test_tasks.py` covers
      the Celery task body — success, failure, cancel, timeout, idempotency,
      reconciliation — and `test_runs.py` covers `RunService` transitions;
      `test_executors.py` covers both execution backends with a fake docker
      client).
- [ ] Run UI tests for login, case management, new run, run detail, and
      artifacts (no frontend test suite exists yet).
- [x] Run a dry-run path with `DRY_RUN=1` (`RunConfigCreate.dry_run` /
      `BenchRunAdapter` support and are covered by tests).
- [x] Verify Tetris still runs through `make bench-run` (unchanged, still
      passes `make check`).
- [x] Verify `make bench-results` still reads current benchmark files (the
      app never writes `runs.yaml`/`results.csv`).
- [x] Verify existing YAML/CSV artifacts are not broken by the web app (same
      reasoning — the app only reads/imports, never overwrites, those files
      except via explicit `sync`).

## Open Decisions

- [x] Decide whether the MVP stores generated DB files under `data/app/` or a new
      local-only path (decided: `data/app/harness_lab.db`, see `config.py`).
- [x] Decide whether delete means hard delete or soft delete for cases
      (decided: soft delete — `CaseService.delete_case` sets
      `status = CaseStatus.deleted` and keeps the row).
- [x] Decide whether case YAML sync is automatic, manual, or both (decided:
      manual only, via the explicit `POST /api/cases/{case_id}/sync` action —
      matches the design spec's "manual sync, not silent background writes").
- [x] Decide whether run IDs are user-entered, generated, or both (decided:
      user-entered — `RunCreate.run_id: str` is a required field, no
      server-side generation).
- [ ] Decide whether login is required in local dev by default (endpoints
      exist but nothing in the frontend enforces a session yet — still open).
- [x] Decide whether the first queue implementation should use an in-process
      worker or a separate worker command (decided: separate Celery worker
      service in Docker Compose running
      `celery -A harness_lab_app.celery_app worker -Q runs`, not in-process
      with the API).

## Definition of Done for MVP

- [ ] User logs in locally (backend ready, no frontend login flow — Phase 3).
- [ ] User creates or edits a case in the UI (create works; edit has no
      frontend form yet — Phase 2/3).
- [x] User runs Tetris from the UI using the existing runner.
- [x] User sees live or near-live logs.
- [ ] User opens artifacts, diff, score, and audit from the run detail page
      (Phase 5, not started).
- [ ] User scores a run (backend endpoint exists; no frontend form — Phase 3/5).
- [ ] User compares at least two runs (`Compare` page is a static shell with
      no real diff — Phase 7).
- [x] Existing CLI benchmark commands still work.
