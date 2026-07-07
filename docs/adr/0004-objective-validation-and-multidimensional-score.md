# ADR-0004: Objective validation and a multidimensional run score

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Marcos Soto (with Claude Code)
- **Related:** ADR-0002 (Celery + Docker-per-run), ADR-0003 (trial provenance),
  `docs/roadmap-local-webapp.md` (Phase 5), `src/harness_lab_app/validation.py`

## Context

Scoring a run was a single manual 0-10 number. To evaluate a harness
scientifically we need to separate *what the produced code objectively does*
(does it run / do tests pass?) from *the human's functional judgment*, from
*what it cost*, from *how much effort it took*. Collapsing these into one number
hides exactly the signal an ablation needs.

The pilot cases have no automated grader (Tetris is "run it and observe"), but
the SDLC prompt already teaches the generated `main.py` to honor `BENCH_SMOKE=1`
for a headless start, and future SWE-bench cases have real test suites — so an
objective floor is achievable and worth standardizing now.

## Decision

We will record a **multidimensional `Validation`** per run (new DB table), with
dimensions kept separate and never collapsed prematurely:

- **objective** — run the case's `validation_commands` (shell strings with a
  `{workspace}` placeholder) in the runner container and record pass/fail. For
  Tetris: a headless SDL-dummy smoke (`BENCH_SMOKE=1 … python {workspace}/main.py`)
  establishing rubric `level_2` ("runs without crashing").
- **cost** — tokens, money, wall time (parsed from `usage.json`).
- **effort** — agent steps and human interventions.
- **subjective** — the manual/LLM functional score (0-10) and optional rubric.
- **composite** — an explicit roll-up: the functional score, **capped at 1.0 if
  objective validation failed** (a high manual score is not credible when the
  code does not run).

Objective validation executes through the same `RunExecutor` abstraction as
runs, so it runs in a sibling container in the **worker** (which owns the docker
socket). It is triggered automatically after a successful run and on demand via
`POST /api/runs/{id}/validate` (enqueued as a Celery task; the API process has
no socket). Pure parsing/spec/composite logic lives in `validation.py` and is
unit-tested without a daemon; orchestration/persistence is in
`ValidationService`.

## Consequences

- Easier: runs carry objective evidence, cost, and effort next to the manual
  score; the composite is transparent and gated by the objective floor; the
  same contract generalizes to bugfix test-runners and SWE-bench eval commands
  (just different `validation_commands`).
- The runner image now bundles `pygame` (installed with `uv pip` into
  `/opt/venv`) — it is the case runtime, not an app dependency. Validation
  commands run under `sh -c` (not `-lc`) so the image's `/opt/venv/bin` stays on
  `PATH`.
- Validation must run in the worker; the API only enqueues. The frontend polls
  `GET /api/runs/{id}/validation`.
- Objective validation is best-effort: a validation failure records an error but
  never changes the run's own success/failure state.

## Alternatives considered

- **Keep a single 0-10 score** — rejected: hides the objective/cost/effort
  signal that harness ablation depends on.
- **Run validation inline in the API** — rejected: the API deliberately has no
  docker socket (ADR-0002); validation is enqueued to the worker instead.
- **A separate hardcoded validator per case type** — rejected in favor of
  case-level `validation_commands`, which generalize to SWE-bench without code.
- **Fold objective pass/fail into the functional score** — rejected: the
  dimensions must stay separate; the composite is where they combine, explicitly.
