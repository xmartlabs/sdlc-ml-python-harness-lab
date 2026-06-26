# Current context

## Project

AI SDLC Lab — a benchmarking harness for AI-assisted development.
Goal: measure which harness + model + prompt combination produces the best result,
at what cost, with what number of iterations. Inspired by SWE-bench methodology:
frozen task + repo/base commit + harness strategy + validation + score + cost.

## Active focus

Executing `tetris-v1` benchmark: 9 experiment cells (A–I) × 3 reps = 27 runs.
Cells A–E use OpenCode with Qwen 35B / GPT-5.5. Cells F–I add Nemotron 120B.
Structural files created 2026-06-25: `task.yaml`, `results.csv`, `README.md`, updated report.

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
| tetris-v1-A-001 | completed | 3 | 1 |
| tetris-v1-A-002 | completed | 5 | 0 |
| tetris-v1-A-003 | running | — | — |
| tetris-v1-B-001 | running | — | — |
| tetris-v1-B-002 to I-003 | pending | — | — |

## Open threads / next steps

- Close A-003 (verify if it stalled or completed).
- Close B-001 (status=running in runs.yaml).
- Pull token/cost data from Langfuse for A-001 and A-002 and populate `results.csv`.
- Begin remaining B–I cells once A-003 is closed.
