# Current context

## Project

AI SDLC Lab — a benchmarking harness for AI-assisted development.
Goal: measure which harness + model + prompt combination produces the best result,
at what cost, with what number of iterations. Inspired by SWE-bench methodology:
frozen task + repo/base commit + harness strategy + validation + score + cost.

## Active focus

Building the Docker-first local web app for Agentic Harness Lab.
Execution backend is now Celery + Redis (broker only) with **one Docker
container per run** (SWE-bench style, ADR-0002): `Dockerfile.runner` bundles
opencode + conductor plugin + uv; the Celery worker (compose service with the
docker socket mounted, concurrency 1) launches sibling runner containers that
bind-mount the host repo rw and reach the host LiteLLM/LM Studio via
`host.docker.internal` (env rewritten from `.env` by `build_run_environment`).
Container logs stream into `RunEvent`; cancel stops the container; retry
persists `resume=True` and re-publishes; orphaned `running` runs are
reconciled to `failed` at worker startup. The old polling `worker.py` was
removed. Web runs are `score_mode=manual` only and finish in
`awaiting_manual_score`.
Pending validation: a real (non-dry-run) Tetris run from the UI against a live
LM Studio + LiteLLM gateway with a populated `.env` (dry-run E2E through
API → Redis → Celery → runner container verified 2026-07-07).

Usability/reliability pass landed 2026-07-07 (branch `feature/web-ui`): fixed the
"set RESUME=1" stale-workspace trap (managed `--fresh` runs + `finalize_workspace`,
ADR-0006), loud preflight (empty executor / placeholder gateway token → 422) and
visible enqueue failure (broker down → run `failed`, not stuck `pending`),
auto-incremental `<case>-K-NNN` run ids + "Run again"/clone, run **delete**
(DELETE `/api/runs/{id}` cascading DB+disk+container, ADR-0007), and a frontend
overhaul (react-router deep-links, collapsible sidebar, state-color chips, dark
mode, per-run cancel/clone/delete). `make check` 145 pass; frontend `vite build`
green. `make app-reset-db PURGE=1` now also clears disk. Still needs a real live
Tetris run from the UI to confirm end-to-end.

## Constraints and decisions in force

- `runs.yaml` is the authoritative run registry — do not modify between reps of a cell.
- Per-run reports go to `data/runs/<run_id>/report.md`. Template: `templates/run-report-template.md`.
- Scoring is always manual at this stage (pygame window, no automated runner).
- `make bench-start RUN=<id>` / `make bench-close RUN=<id> SCORE=<n> INTERVENTIONS=<n>` are the gates.
- `results.csv` accumulates only completed + scored runs (not pending).
- `make bench-results` reads `runs.yaml` — it shows all runs including pending ones.
  `results.csv` is the clean comparison artifact for scored runs only. These are different things.
- `bench_start.py` currently injects `prompt_incremental.md` for all tetris-v1 runs.
  For conductor/Nemotron cells (B–I), verify the correct prompt manually before running.

## Current run state

| Run | Status | Score | Interventions |
|-----|--------|-------|---------------|
| local-webapp-mvp | in progress | — | — |

## Scientific-lab direction (agreed 2026-07-07)

The lab is being shaped into an instrument to evaluate the *engineering harness*
(skills + docs + hooks + agents) as the **independent variable**, across
cases/prompts/models incl. future SWE-bench. Vocabulary: case (frozen task),
arm/condition (harness fingerprint + prompt + model + params), trial (one run),
cell (case×condition, N reps), experiment (matrix varying ONE factor). Two
method pillars: causal attribution needs **ablation** (toggle one factor,
re-run, compare distributions), not observation; and local models at temp>0
mean **statistical** reproducibility (N reps, mean±spread), not deterministic
replay.

Pillar order chosen: (1) provenance/fingerprint ✅ DONE, (2) objective
validation + multidimensional score ✅ DONE (ADR-0004), (3) structured harness
attribution ✅ DONE (ADR-0005), (4) ablation/experiment runner (NEXT). See
ADR-0003/0004/0005.

Pillar 3 delivered: `attribution.py` parses a run's opencode step logs into a
machine-readable `Attribution` (tools read/edit/bash/shell, files read/edited,
governance-doc read coverage, bash + agent-mode histograms), cross-referenced
with the available skills from metadata. Pure log parsing (no container) → runs
inline in the API and auto-indexed after every run (success or fail).
`GET/POST /api/runs/{id}/attribution`, panel in RunDetail. Honest limit: skill
*invocation* isn't file-observable (skills are context-injected), so
skills_consulted counts only SKILL.md reads (a lower bound); documented in the
`skills_note` field, not faked. Verified on K-001: read 5/8 governance docs,
edited only PLAN.md + main.py, python×16 self-tests, agent mode build.
Fixed a real bug: opencode's `"touching file"` is the watcher (fires on reads),
not an edit — edits come only from `permission=edit`.

Pillar 2 delivered: a `Validation` per run (`validation.py`,
`validation_service.py`, `models.Validation`) with separate objective / cost /
effort / subjective dimensions + an explicit `composite` gated by the objective
floor. Objective validation runs the case's `validation_commands` (shell with
`{workspace}` placeholder) in a runner container via the executor — auto after a
successful run and on demand via `POST /api/runs/{id}/validate` (enqueued to the
worker, which owns the docker socket). Tetris uses a headless smoke
(`BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy ... python {workspace}/main.py`); the
runner image now bundles pygame. Verified E2E on the real K-001 Tetris:
objective pass, cost 206961 tokens, effort 8 steps, composite 7.0 after a
manual functional score.

Git hygiene: run artifacts are no longer tracked — `data/runs/**` is git-ignored
(only `.gitkeep`/`.gitignore` remain); operational run state lives in the DB and
on disk, never in git. The CLI (`runs.yaml`/`results.csv`) is kept as a
deprecated legacy path; the scalable target is the DB-only web server.

Provenance delivered: `src/harness_lab_app/provenance.py` +
`bench_run.write_metadata` now write a hashable trial condition into
`metadata.json`: `condition_hash` (arm id), `harness_fingerprint`
(skills-lock hashes + agents + governance docs + hooks + opencode/plugin
versions + rolled `harness_hash`), `model_condition` (temp/context/alias),
`prompt_hash`, `case_hash`, `lab_git_commit`, `harness_git_dirty`,
`runner_image`. Verified on a real run: K-002 metadata carried a full manifest.

## Open threads / next steps

- Pillar 4 (ablation/experiment runner) — NEXT: matrix case×arm varying ONE
  harness factor, N reps, compare distributions using `condition_hash`
  (ADR-0003) as the grouping key, with the multidimensional `Validation`
  (ADR-0004) + `Attribution` (ADR-0005) as the per-trial outcome features.
- Deeper skill attribution: have the harness emit skill-invocation events so
  `skills_consulted` stops being a SKILL.md-read lower bound.
- Add a `bugfix-with-tests` validation_commands (pytest) to exercise the "tests"
  strategy (only Tetris smoke exists so far).
- The web app should index `metadata.json` and group runs by `condition_hash`
  (foundation for the Phase 7 comparison surface).
- DB-only migration: make the runner read run definitions from the DB so
  `runs.yaml`/`results.csv` can leave git entirely (currently kept as legacy CLI
  state the runner still needs).
- For rigorous trials, commit the harness first so `harness_git_dirty=false`.
- Tail `data/runs/<run_id>/steps/*.log` when available (still open in Phase 4).
- Add richer run detail tabs for artifacts, diff, score, and audit (Phase 5).
  Backend artifact indexing already exists (`ArtifactService.index_run`); the
  gap is entirely frontend (no artifacts list/download UI yet).
- Expand the case wizard into edit/delete/clone flows (backend endpoints for
  all three already exist in `CaseService`/`api.py`; only frontend buttons
  are missing).
- Add a frontend login flow and session gating (backend `POST
  /api/auth/login`/`logout` exist; still the main open item under Phase 3).
- `docs/roadmap-local-webapp.md` was fully audited 2026-07-07 against actual
  code/tests/frontend — every checkbox now reflects a verified implementation
  state, with notes distinguishing "backend done, no UI yet" from "declared
  but never reachable" from "not started." Keep it checked as the MVP grows.
