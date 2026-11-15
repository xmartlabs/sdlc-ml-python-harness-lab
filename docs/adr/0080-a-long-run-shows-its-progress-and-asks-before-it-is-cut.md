# ADR-0080: A long run shows its progress, and asks before it is cut

- **Status:** Accepted
- **Date:** 2026-11-14
- **Deciders:** Marcos Soto
- **Related:** [ADR-0048](0048-a-check-that-did-not-measure-is-not-a-failure.md),
  [ADR-0062](0062-a-check-the-environment-failed-is-not-a-verdict.md),
  [ADR-0076](0076-per-step-reward.md),
  [ADR-0079](0079-a-hard-case-is-paced-in-phases-and-cannot-read-its-grade.md)

## Context

`racing3d-v1-K-001` (claude-opus-5-5, five phases) exposed four problems with how the
lab supervised long runs:

- **The budget was measured on a clock that stops.** The watchdog used
  `time.monotonic()` inside the worker, which runs in Docker Desktop's VM. While the
  laptop slept, that clock stood still. The run lasted 5 h 13 min of real time; the
  worker counted 1 h 29 min, so the two-hour budget never fired.
- **Nothing showed progress.** The run page offered the raw log. It did not say which
  phase was running, how long each phase had taken, what each had cost, or that the
  last line was 28 minutes old. Phase 5 sat 43 minutes inside a silent
  `timeout 1500 godot-render` test with nothing on screen saying so.
- **One fixed budget suits no phase.** Phases 1–3 took 5–14 minutes; phase 4 took
  52 minutes of real work. A number tight enough to catch a wedged phase cuts a
  legitimate one, and a number loose enough for phase 4 waits out a wedged phase 5.
- **A cut threw the work away.** A run stopped for time was never validated, so four
  finished phases left no grade.

## Decision

1. **Budgets run on the wall clock.** The watchdog reads `time.time()` through one
   function, `tasks._wall_clock`. Time the host spent asleep is time the operator
   waited, and it counts.
2. **A cut run is graded on the work it left.** After a `timeout`, `stalled`,
   `slow_phase` or `cancelled` stop:
   - the worker archives the live workspace to `data/runs/<id>/workspace` (the
     runner never reaches its own archive step once stopped);
   - it runs the objective validation and the per-step reward;
   - the run stays `failed` with its reason, and the grade sits beside it.

   A run stopped because the model never answered (`model_silent`, `endpoint`)
   measured nothing and is not graded.
3. **Progress is read from the files a run writes.** `GET /api/runs/{id}/progress`
   (`run_progress.read_progress`) returns, per step:
   - the phase name from the prompt's `## Phase N of M — name`;
   - its duration, its turns, and its cost once its `result` event exists;
   - the last action;
   - the phase's live grade.

   It also returns how long the run has been quiet. Nothing new is stored for it.
   The run page shows it as a strip of phases above the log, refreshed every five
   seconds.
4. **Each phase is graded as it ends.** A background thread
   (`phase_watch.LivePhaseGrader`) grades every finished snapshot with the same
   spec, executor and parser as the final validation
   (`ValidationService.grade_step_live`) and writes `live_steps.json`.
   - It runs one grade at a time, newest first.
   - It installs the graders' dependencies once.
   - It never touches the run: a failed grade is recorded as such.
   - The final validation waits for a grade in flight, for as long as one pass
     can take (every command at its own deadline). It reinstalls the same
     dependency folder and regrades the same snapshots, so the two must not
     overlap.
5. **A slow phase asks before it is cut.** Each phase has an expected time:
   - the case's `phase_budget_minutes`;
   - else three times the median of that phase in the case's earlier runs;
   - never below `run_phase_floor_seconds` (45 min).

   Past it, `phase_watch.PhaseWatch` flags the run (`slow_phase.json` plus an event),
   and the page offers **Keep waiting**, which adds `run_phase_extend_seconds` via
   `POST /api/runs/{id}/keep-waiting` into `Run.extra_wait_seconds`, or **Stop and
   grade**, which is the existing cancel.

   The column is the run's running total. Each phase counts only what was
   granted while it ran, so time given to one phase is not carried into the next.

   With no answer within `run_slow_grace_seconds` (15 min), the case's `on_slow`
   applies: `stop` (the default, for unattended experiment runs) cuts the run as
   `Phase too slow:`, a timeout-kind verdict, and grades it; `wait` keeps it running.
6. **A paced case sets its own ceiling.** `run_timeout_minutes` in `task.yaml`
   replaces the two-hour default for that case. The three game cases declare
   360 minutes, `phase_budget_minutes: 75` and `on_slow: wait`: watched phase by
   phase, flagged when slow, never cut without a person while someone can answer.

## Consequences

- A sleeping laptop can no longer stretch a run beyond its budget.
- The run page answers "is it doing anything?" without reading the log: phase, time,
  cost, last action, quiet time, and how far each finished phase got.
- A slow phase becomes a decision, with a default for when nobody is there to make
  it.
- A run cut at phase 5 of 5 is graded on phases 1–4 instead of recording nothing.
- Verified end to end with the worker's own code and Docker. In a three-phase run
  with a 40-second budget and a 30-second grace, where phase 3 hangs:
  - live grades of 1/3 and 2/3 appeared as phases 1 and 2 ended;
  - phase 3 was flagged at its deadline and stopped as `slow_phase` when the grace
    ran out;
  - the run was graded 2/3 on what it had built.
- Live grading adds one grader run per phase while the agent works. For the game
  cases that is a Godot harvest of about a minute, in its own container.
- `extra_wait_seconds` is a new `run` column, added by `init_db` like every other.
