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

## Open threads / next steps

- Validate a real Tetris run from the UI end-to-end (needs: `.env` with
  `GATEWAY_BASE_URL`/`GATEWAY_TOKEN`, LiteLLM on :4000, LM Studio on :1234
  serving the qwen model — none of these were present on the host on
  2026-07-07; opencode is also not installed on the host, only in the runner
  image now).
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
