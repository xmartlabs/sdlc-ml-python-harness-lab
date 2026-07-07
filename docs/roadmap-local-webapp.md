# Roadmap: Local Web App for Agentic Harness Lab

## Purpose

Build a local web app for configuring, running, inspecting, scoring, and auditing
agentic benchmark runs. The app must start by wrapping the existing CLI workflow
instead of replacing it.

Tetris is the pilot case. The product model must support new benchmark cases from
the beginning: single-prompt tasks, multi-step tasks, repo tasks, bugfix tasks, and
future SWE-bench-style instances.

## Guiding Decisions

- [ ] Keep `scripts/bench_run.py` and `make bench-*` as the first execution backend.
- [ ] Keep `benchmarks/*/task.yaml`, `runs.yaml`, `results.csv`, and `data/runs/`
      compatible with the current CLI workflow.
- [ ] Use the database as the web app's operational state.
- [ ] Use the filesystem as the artifact store for the MVP.
- [ ] Treat case management as a primary product area, not a hidden settings page.
- [ ] Run a UI/product brainstorming pass before implementing frontend screens.
- [ ] Design for local, single-user operation first, without blocking future
      multi-user use.

## Phase 0: Current Contract and Sources of Truth

Goal: document the current harness contract so the web app wraps the real system.

- [ ] Inventory benchmark definitions in `benchmarks/*/task.yaml`.
- [ ] Inventory run registries in `benchmarks/*/runs.yaml`.
- [ ] Inventory scored result tables in `benchmarks/*/results.csv`.
- [ ] Inventory run artifacts in `data/runs/<run_id>/`.
- [ ] Inventory active workspaces in `workspace/<run_id>/`.
- [ ] Document `scripts/bench_run.py` CLI arguments and behavior.
- [ ] Document `make bench-run`, `make bench-results`, `make bench-start`,
      `make bench-close`, `make bench-audit`, `make bench-agents-audit`, and
      `make bench-reset`.
- [ ] Define which state the web app reads from YAML/CSV/filesystem.
- [ ] Define which state the web app writes to DB only.
- [ ] Define which actions still call the existing CLI.
- [ ] Confirm Tetris remains a pilot, not a special-case domain model.

Acceptance criteria:

- [ ] A developer can explain where cases, runs, scores, logs, diffs, usage, and
      audits live today.
- [ ] The app contract says when DB state and CLI artifacts must stay in sync.
- [ ] No existing CLI command is removed or made obsolete by the roadmap.

## Phase 1: Product and UI Design with Brainstorming

Goal: design the user experience before building UI.

- [ ] Run the `brainstorming` skill before frontend implementation starts.
- [ ] Produce a UI design spec and save it before coding the frontend.
- [ ] Define the main navigation structure.
- [ ] Design the dashboard view.
- [ ] Design the case management view.
- [ ] Design the create/edit case flow.
- [ ] Design the new run flow.
- [ ] Design the run detail view.
- [ ] Design logs, artifacts, diff, score, and audit panels.
- [ ] Design the comparison view.
- [ ] Define empty states for no cases, no runs, no artifacts, no logs, and no
      audits.
- [ ] Define loading, running, failed, cancelled, and completed states.
- [ ] Define how long forms handle prompt bodies, rubrics, constraints, validation
      commands, and metadata.
- [ ] Define visual style, density, typography, color use, table behavior, and
      panel layout.

Design defaults:

- Use a workbench-style product UI rather than a marketing-style landing page.
- Make case management a top-level nav item.
- Favor dense, scannable tables for repeated operational work.
- Keep logs and diffs readable before adding decorative polish.
- Use clear state badges for run lifecycle state.

Acceptance criteria:

- [ ] The design spec covers all primary screens and states.
- [ ] The design supports both single-prompt and multi-step cases.
- [ ] The design makes run setup fast for repeated benchmark work.
- [ ] The design can be implemented without inventing screens during coding.

## Phase 2: Persistent Case Model

Goal: let users create, edit, delete, clone, and persist benchmark cases in DB.

- [ ] Add a persistent `cases` model.
- [ ] Add persistent prompt variants for each case.
- [ ] Add persistent case validation settings.
- [ ] Add persistent case rubric settings.
- [ ] Add persistent case constraints.
- [ ] Add persistent case metadata.
- [ ] Add create, read, update, delete, clone, activate, and deactivate operations.
- [ ] Seed Tetris from `benchmarks/tetris/task.yaml`.
- [ ] Support export or sync to `benchmarks/<case>/task.yaml`.
- [ ] Track whether a case was imported, created in UI, cloned, or synced from YAML.

Case types:

- [ ] `single_prompt`
- [ ] `multi_step`
- [ ] `build_from_scratch`
- [ ] `bugfix`
- [ ] `repo_task`
- [ ] `swe_bench_instance`

Minimum case fields:

- [ ] Case ID.
- [ ] Name.
- [ ] Description.
- [ ] Type.
- [ ] Status.
- [ ] Tags.
- [ ] Subject repository.
- [ ] Base commit.
- [ ] Prompt variants.
- [ ] Step prompts for multi-step cases.
- [ ] Success criteria.
- [ ] Scoring rubric.
- [ ] Constraints.
- [ ] Validation commands.
- [ ] Timeout.
- [ ] Max steps.
- [ ] Allowed files.
- [ ] Metadata.

Acceptance criteria:

- [ ] A user can create a single-prompt case from the UI.
- [ ] A user can create a multi-step case from the UI.
- [ ] A user can edit a case without editing YAML by hand.
- [ ] A user can delete or deactivate a case safely.
- [ ] A user can clone Tetris into a new editable case.
- [ ] The web app can still read the existing Tetris case.

## Phase 3: Minimal Web App over the CLI

Goal: ship the first usable local app without rebuilding the runner.

Backend:

- [ ] Add a local FastAPI app.
- [ ] Add simple local auth.
- [ ] Add case endpoints.
- [ ] Add run endpoints.
- [ ] Add artifact endpoints.
- [ ] Add log endpoints.
- [ ] Add score endpoints.
- [ ] Add audit endpoints.
- [ ] Add health and configuration endpoints.
- [ ] Execute runs by calling `uv run python scripts/bench_run.py` or
      `make bench-run`.

Frontend:

- [ ] Add a React/Vite frontend.
- [ ] Add login.
- [ ] Add dashboard.
- [ ] Add case management.
- [ ] Add case create/edit/delete/clone flows.
- [ ] Add new run flow.
- [ ] Add run list.
- [ ] Add run detail.
- [ ] Add artifacts view.
- [ ] Add logs view.
- [ ] Add diff view.
- [ ] Add score panel.
- [ ] Add audit panel.

Initial API surface:

- [ ] `POST /api/auth/login`
- [ ] `POST /api/auth/logout`
- [ ] `GET /api/cases`
- [ ] `POST /api/cases`
- [ ] `GET /api/cases/{case_id}`
- [ ] `PATCH /api/cases/{case_id}`
- [ ] `DELETE /api/cases/{case_id}`
- [ ] `POST /api/cases/{case_id}/clone`
- [ ] `POST /api/cases/{case_id}/sync`
- [ ] `GET /api/runs`
- [ ] `POST /api/runs`
- [ ] `GET /api/runs/{run_id}`
- [ ] `POST /api/runs/{run_id}/start`
- [ ] `POST /api/runs/{run_id}/cancel`
- [ ] `POST /api/runs/{run_id}/retry`
- [ ] `POST /api/runs/{run_id}/score`
- [ ] `POST /api/runs/{run_id}/audit`
- [ ] `GET /api/runs/{run_id}/logs`
- [ ] `GET /api/runs/{run_id}/artifacts`
- [ ] `GET /api/runs/{run_id}/artifacts/{artifact_name}`

Acceptance criteria:

- [ ] A user can log in locally.
- [ ] A user can create a case.
- [ ] A user can select a case and configure a dry run.
- [ ] A user can start a Tetris run through the web app.
- [ ] A user can inspect the generated run record.
- [ ] The CLI workflow still works after using the web app.

## Phase 4: Queue, Lifecycle States, and Live Logs

Goal: run benchmarks without blocking the web server.

- [ ] Add a local run queue.
- [ ] Persist queue jobs in DB.
- [ ] Add a worker process.
- [ ] Stream process stdout and stderr to run events.
- [ ] Tail `data/runs/<run_id>/steps/*.log` when available.
- [ ] Expose live logs through SSE or WebSocket.
- [ ] Support cancellation when the process is still running.
- [ ] Support retry for failed runs.
- [ ] Support resume when `bench_run.py --resume` applies.
- [ ] Record run start time, end time, duration, exit code, and failure reason.

Run states:

- [ ] `draft`
- [ ] `pending`
- [ ] `running`
- [ ] `awaiting_manual_score`
- [ ] `scoring`
- [ ] `auditing`
- [ ] `completed`
- [ ] `failed`
- [ ] `cancelled`

Acceptance criteria:

- [ ] Starting a run returns immediately.
- [ ] The run continues in a worker.
- [ ] Logs appear while the process is active.
- [ ] Failed runs show useful failure details.
- [ ] Cancel, retry, and resume are visible only when valid for the current state.

## Phase 5: Artifacts, Diff, Score, and Audit

Goal: make each run inspectable from the browser.

- [ ] Index run artifacts from `data/runs/<run_id>/`.
- [ ] Show `metadata.json`.
- [ ] Show `prompt.md`.
- [ ] Show `usage.json`.
- [ ] Show `diff.patch`.
- [ ] Show `report.md`.
- [ ] Show `audit.md`.
- [ ] Show step logs from `steps/step-*.log`.
- [ ] Add artifact download links.
- [ ] Add manual score entry.
- [ ] Add human intervention entry.
- [ ] Add notes entry.
- [ ] Close scored runs through the same contract as `bench-close`.
- [ ] Launch descriptive run audit through the same contract as `bench-audit`.
- [ ] Show audit status and final audit content.

Acceptance criteria:

- [ ] A user can inspect all standard artifacts for `tetris-v1-J-002`.
- [ ] A user can score a run from the UI.
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

- [ ] Backend: FastAPI.
- [ ] Frontend: React with Vite.
- [ ] MVP database: SQLite.
- [ ] Later database: PostgreSQL.
- [ ] MVP queue: local DB-backed worker or lightweight process queue.
- [ ] Later queue: Celery and Redis.
- [ ] Auth: local username and password.
- [ ] Artifact storage: filesystem under `data/runs`.
- [ ] Tracking integrations: consume Langfuse, LiteLLM, and MLflow outputs when
      present; keep them optional.

## Validation Plan

- [ ] Run API unit tests for case CRUD.
- [ ] Run API unit tests for run creation.
- [ ] Run API tests for artifact indexing.
- [ ] Run worker tests for valid lifecycle transitions.
- [ ] Run UI tests for login, case management, new run, run detail, and artifacts.
- [ ] Run a dry-run path with `DRY_RUN=1`.
- [ ] Verify Tetris still runs through `make bench-run`.
- [ ] Verify `make bench-results` still reads current benchmark files.
- [ ] Verify existing YAML/CSV artifacts are not broken by the web app.

## Open Decisions

- [ ] Decide whether the MVP stores generated DB files under `data/app/` or a new
      local-only path.
- [ ] Decide whether delete means hard delete or soft delete for cases.
- [ ] Decide whether case YAML sync is automatic, manual, or both.
- [ ] Decide whether run IDs are user-entered, generated, or both.
- [ ] Decide whether login is required in local dev by default.
- [ ] Decide whether the first queue implementation should use an in-process worker
      or a separate worker command.

## Definition of Done for MVP

- [ ] User logs in locally.
- [ ] User creates or edits a case in the UI.
- [ ] User runs Tetris from the UI using the existing runner.
- [ ] User sees live or near-live logs.
- [ ] User opens artifacts, diff, score, and audit from the run detail page.
- [ ] User scores a run.
- [ ] User compares at least two runs.
- [ ] Existing CLI benchmark commands still work.
