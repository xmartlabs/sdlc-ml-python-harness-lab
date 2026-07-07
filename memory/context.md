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
validation + multidimensional score on existing cases (Tetris headless smoke +
bugfix test-runner) — NOT SWE-bench yet, (3) structured harness attribution,
(4) ablation/experiment runner. See `docs/adr/0003-trial-provenance-and-harness-fingerprint.md`.

Provenance delivered: `src/harness_lab_app/provenance.py` +
`bench_run.write_metadata` now write a hashable trial condition into
`metadata.json`: `condition_hash` (arm id), `harness_fingerprint`
(skills-lock hashes + agents + governance docs + hooks + opencode/plugin
versions + rolled `harness_hash`), `model_condition` (temp/context/alias),
`prompt_hash`, `case_hash`, `lab_git_commit`, `harness_git_dirty`,
`runner_image`. Verified on a real run: K-002 metadata carried a full manifest.

## Open threads / next steps

- Build pillar 2: per-case validation contract returning structured scores —
  Tetris headless smoke (`BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy python main.py`)
  + a test-runner for `bugfix-with-tests`; separate objective (tests) from
  subjective (manual/LLM rubric) from cost (usage.json) from effort (steps,
  interventions).
- For rigorous trials, commit the harness first so `harness_git_dirty=false`
  and trials are attributable to a clean `lab_git_commit`.
- The web app should index `metadata.json` and group runs by `condition_hash`
  (foundation for the Phase 7 comparison/ablation surface).
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
