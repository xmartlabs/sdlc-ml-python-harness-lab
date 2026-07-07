# Local Web App Design

## Summary

Build a Docker-first local web app for operating Agentic Harness Lab benchmark
runs. The MVP is an operational workbench over the current CLI runner, not a
replacement for it. It uses Tetris as the pilot case while supporting new
single-prompt, multi-step, repo-task, bugfix, and future SWE-bench-style cases.

The chosen product direction is an **Operational MVP with its own database**.
The app stores web-created cases, run configs, runs, queue events, and indexed
artifacts in DB. It keeps `scripts/bench_run.py`, `make bench-*`,
`benchmarks/*/task.yaml`, `runs.yaml`, `results.csv`, `data/runs/`, and
`workspace/` compatible with the existing CLI workflow.

## Product Shape

The main experience is a **Run Workbench**. After local login, the user lands on
work, not a marketing page. The primary flow is:

1. View active and recent runs.
2. Create or select a benchmark case.
3. Configure prompt, harness, model, max steps, score mode, and tracking.
4. Preview the generated command or dry-run plan.
5. Start the run.
6. Follow logs and lifecycle state.
7. Inspect artifacts, diff, metadata, usage, and report.
8. Enter manual score and interventions when needed.
9. Launch or read audit output.
10. Compare runs.

The selected layout is **Operational Shell**:

- Left sidebar: `Dashboard`, `Runs`, `Cases`, `Compare`, and `Settings`.
- Center workbench: active queue, run list, filters, and run setup.
- Right inspector: selected run status, actions, score, artifacts, audit, and
  quick metadata.

The selected visual direction is **Quiet Operations**: light, restrained, dense,
and readable. Tables, forms, logs, diffs, and state badges take priority over
decorative UI. The app should support long sessions without visual fatigue.

## Architecture

The recommended operating mode is **Docker Compose**. Direct local execution is
allowed for development, but the supported operational path runs the web app in
containers to reduce Python, Node, OS, path, and worker incompatibilities across
macOS and Linux.

Initial Compose services:

- `frontend`: React/Vite UI.
- `api`: FastAPI backend.
- `worker`: benchmark job worker.
- `db`: persistent operational database.

Future Compose profiles:

- `tracking`: Langfuse, LiteLLM, MLflow, or local tracking helpers when managed
  inside this repo.
- `platform`: distributed workers, object storage, and automated evaluators.

The repo is mounted into the API and worker containers so they can access
`benchmarks/`, `scripts/`, `data/runs/`, `workspace/`, and `.env`-driven runtime
configuration. The first runner adapter invokes `scripts/bench_run.py` or
`make bench-run`. Later adapters may invoke SWE-bench evaluators, isolated
harnesses, or distributed workers.

## Source of Truth

The MVP uses explicit ownership rules:

- DB is the source of truth for cases created or edited in the web UI.
- YAML files are imported from and synced to by explicit user action.
- `data/runs/<run_id>/` remains the source of truth for run artifacts.
- `runs.yaml` and `results.csv` remain compatibility artifacts for the CLI flow.
- The UI never writes benchmark YAML implicitly.

Case sync is manual. A user-created or edited case can show one of these sync
states: `not_synced`, `synced`, `modified_since_sync`, or `imported_from_yaml`.
The UI exposes an explicit `Sync to benchmark files` action that writes or
updates `benchmarks/<case>/task.yaml` and related prompt files according to the
case type.

## Core Domain

The MVP domain has four main entities.

`Case` describes a benchmark task. It includes ID, name, description, type,
status, tags, subject repository, base commit, prompt variants, step prompts,
success criteria, scoring rubric, constraints, validation commands, timeout,
max steps, allowed files, metadata, and sync state.

`RunConfig` captures an executable configuration for a case. It includes case,
prompt variant, harness, planner model, executor model, score mode, max steps,
resume flag, dry-run flag, tracking options, environment references, and runner
adapter.

`Run` represents a concrete execution. It includes run ID, config snapshot,
state, queue metadata, start and end timestamps, duration, exit code, failure
reason, score, interventions, notes, and links to artifacts and events.

`Artifact` indexes files produced under `data/runs/<run_id>/`. Standard artifact
names include `metadata.json`, `prompt.md`, `usage.json`, `diff.patch`,
`report.md`, `audit.md`, `opencode.log`, and `steps/step-*.log`.

Run states are:

- `draft`
- `pending`
- `running`
- `awaiting_manual_score`
- `scoring`
- `auditing`
- `completed`
- `failed`
- `cancelled`

## Case Authoring

Case management is a top-level product area. The MVP supports create, list,
view, edit, clone, deactivate, delete, import from YAML, and manual sync to YAML.

The chosen creation pattern is **Templates first + short wizard**:

1. Select a template: `single_prompt`, `multi_step`, `build_from_scratch`,
   `bugfix`, `repo_task`, or `swe_bench_instance` draft.
2. Fill required basics: ID, name, description, tags, repository, and base
   commit when relevant.
3. Add prompts: one prompt for `single_prompt`, ordered steps for `multi_step`,
   and issue/task text for repo-oriented cases.
4. Define success criteria, rubric, constraints, validation command, timeout,
   max steps, and allowed files.
5. Review readiness checks and save.

Readiness checks flag missing prompt content, missing rubric, missing validation
for automated cases, invalid case ID, missing repository/base commit for repo
tasks, unsynced YAML, and unsupported runner adapter combinations.

Tetris is seeded from `benchmarks/tetris/task.yaml` and can be cloned into a new
editable case.

## Run Execution Flow

The MVP run flow is:

1. User opens `New Run`.
2. User selects case, prompt, harness, planner model, executor model, score mode,
   max steps, and tracking settings.
3. API creates a `RunConfig`.
4. API generates a command preview and optional dry-run preview.
5. User starts the run.
6. API creates a `Run` with `pending` state.
7. Worker claims the job and switches state to `running`.
8. Worker invokes the runner adapter.
9. Worker stores stdout, stderr, lifecycle events, exit code, and failure reason.
10. Backend indexes files under `data/runs/<run_id>/`.
11. Run moves to `awaiting_manual_score`, `completed`, or `failed`.
12. User enters score/interventions/notes or launches audit from the inspector.

Cancel, retry, and resume are shown only when valid for the current state and
runner adapter.

## Screens

`Dashboard` shows active runs, recent runs, queue state, basic score summaries,
cost/token/duration summaries when available, and runtime health.

`Runs` is the primary workbench. It shows filters, active jobs, recent runs, a
`New Run` action, and the right-side inspector for the selected run.

`Cases` lists all cases and exposes create, edit, clone, deactivate, delete,
import, and sync actions. Case editing uses the template-first wizard and then an
expert edit screen for later changes.

`Run Detail` is available through the inspector and direct route. It has tabs:
`Overview`, `Logs`, `Artifacts`, `Diff`, `Score`, and `Audit`.

`Compare` starts with two-run comparison. It compares status, config, score,
duration, tokens, cost, diff, artifacts, and audit summaries.

`Settings` contains local auth settings, repo paths, Docker/runtime health,
runner adapters, tracking configuration, and sync defaults.

## Error Handling

Errors appear in the context where the user can act. The UI shows command,
exit code, last log lines, missing artifact name, missing configuration field,
runtime health failure, unsynced case state, or invalid lifecycle transition.

Missing artifacts render clear empty states. A run with no audit shows an action
to start audit when artifacts are sufficient. A case with unsynced YAML shows a
manual sync action, not silent background writes.

## Testing and Validation

Tetris is the first validation path. The MVP must support:

- Import or seed `tetris-v1`.
- Create a run with `DRY_RUN=1`.
- Execute a real Tetris run when runtime dependencies are available.
- Inspect existing artifacts for `data/runs/tetris-v1-J-002`.
- Enter manual score.
- Launch or read audit output.
- Compare at least two runs.

Automated tests cover:

- Case CRUD and clone behavior.
- Template-first case wizard validation.
- Manual YAML sync state transitions.
- Run config creation.
- Command preview generation.
- Queue lifecycle transitions.
- Worker execution with a fake runner adapter.
- Artifact indexing.
- Log reading or streaming fallback.
- Manual score submission.
- Audit request lifecycle.
- Two-run comparison.
- UI flow from login to case creation to new run to run detail.

## Growth Path to Platform

The MVP is designed as a stepping stone to a platform architecture. The stable
boundaries are runner adapters, case model, run lifecycle, artifact contract, and
evaluation model.

Future iterations can add:

- SWE-bench import and execution.
- Automated evaluators.
- Celery/Redis or remote workers.
- Object storage for artifacts.
- Distributed execution pools.
- Rich model x harness x prompt matrices.
- Multi-user auth and role-based access.
- Managed tracking services in Docker Compose profiles.

These changes should not require replacing the Operational Shell UI or the
Case/Run/Artifact model.

## Acceptance Criteria

- The design uses Docker Compose as the recommended operating mode.
- The MVP can run over the existing CLI without breaking `make bench-*`.
- Case management is top-level and supports UI-created cases.
- Cases created in UI persist in DB and sync to YAML only by explicit action.
- Tetris is supported as the pilot case.
- The UI exposes run setup, live or near-live logs, artifacts, diff, score, and
  audit.
- The architecture can evolve from Operational MVP to platform without
  replacing core boundaries.
