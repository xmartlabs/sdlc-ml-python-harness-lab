# Benchmarks

Each subdirectory is one benchmark task: frozen prompts, a `scoring.md`, a `runs.yaml`
registry, and a `results/` folder.

## Active

- **`tetris/`** — `tetris-v1`. Game-build task across 9 experiment cells (A–I), 3 reps each.
  The first benchmark; optimizes for a clear 0–10 functional ladder.
  See `tetris/README.md` for the operator quickstart.
- **`nemotron-planner-qwen-executor/`** — planner/executor split benchmark focused on
  `nemotron-super-120b` as planner and local executors (`qwen3.6-35b`, `qwen3.5-9b`).
  Goal: test whether a stronger planner improves local execution quality and reduces loops.
- **`nemotron-all-120b/`** — end-to-end benchmark where both planner and executor are
  `nemotron-super-120b`. Goal: evaluate if a non-GPT large model can complete full tasks in
  the same harness with reproducible artifacts.

## Planned (post-Tetris — more business value than a game)

- **`bugfix-with-tests/`** — given a red test, fix the implementation without touching the test.
  Deterministic pass/fail; strong signal on real work. Metrics: pass/fail, diff size, turns, tokens.
- **`fastapi-endpoint/`** — add `GET /health/details` with a test. Measures architecture + tests + execution.
- **`snake/`** — second small game, to check whether Tetris findings generalize.

Future ideas: refactor a file (behavior-preserving, tests green) · RAG mini-pipeline
(loader + chunker + mock retriever + test) · external-API tool wrapper with retry/errors + tests.
