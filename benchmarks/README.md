# Benchmarks

Each subdirectory is one benchmark task: frozen prompts, a `scoring.md`, a `runs.yaml`
registry, and a `results/` folder.

## Active

- **`tetris-v2/`** — build a Tetris in pygame, graded by a shipped pytest suite through
  a `Game` contract: ten objective assertions, so `test_pass_rate` has room to move and
  the judge is not the only graded signal (ADR-0050). The lab's base case for harness
  experiments; it replaced the manually-scored `tetris-v1` pilot.
- **`cli-textstats/`** — `cli-textstats-v1`. A small stdlib CLI with three exact-stdout
  checks; the regression suite's simple-feature canary.
- **`todo-web-v1/`** — the first frontend case: an accessible, offline todo app graded in
  headless Chromium by twelve Playwright tests through its accessible contract (keyboard
  flow, `aria-pressed`, persistence, HTML escaping). Measures frontend quality and
  capability expansion outside a Python stack (ADR-0067). Needs the browser layer in
  `Dockerfile.runner`.
- **`frontend-bugfix-v1/`** — a clinic-schedule page that "mostly works", seeded into the
  workspace from `starter/` (ADR-0068): four bugs (interaction, state, accessibility,
  injection), ten browser tests, two of them regressions so neither leaving the page alone
  nor deleting it scores.
- **`clinic-slots-t1/`, `clinic-booking-t2/`, `clinic-rules-t3/`** — the clinic ladder:
  appointment scheduling in three difficulty tiers (`tier:1`-`tier:3`), pure Python. The
  experiment page shows, per arm, the highest tier done reliably (ADR-0069). See
  `clinic-slots-t1/README.md`.
- **`harness-regression-v1/`** — not a case: the suite definition (`suite.yaml`) that
  pairs the cases above for a baseline-vs-candidate harness comparison (ADR-0037).
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
