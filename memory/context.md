# Current context

## Project

AI SDLC Lab — a benchmarking harness for AI-assisted development.
Goal: measure which harness + model + prompt combination produces the best result,
at what cost, with what number of iterations. Inspired by SWE-bench methodology:
frozen task + repo/base commit + harness strategy + validation + score + cost.

## Active focus

Building the Docker-first local web app for Agentic Harness Lab.
The MVP now has backend case/run/artifact services, Tetris seeding, a frontend
with case and run creation forms, and a worker that streams run stdout/stderr
into `RunEvent` incrementally (polling-based live logs, no SSE/WebSocket yet).
Cancel now actually terminates a live worker subprocess (DB-flag signal +
`Popen.terminate()`), retry rebuilds the command with `--resume` forced, and
both actions are exposed as buttons in the run detail inspector.
Next concrete step is a real run queue (currently one-pending-at-a-time,
sequential across the whole app).

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

- Worker still processes one pending run per poll cycle (`run_pending_once`);
  no queue depth or concurrency yet — next Phase 4 item.
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
