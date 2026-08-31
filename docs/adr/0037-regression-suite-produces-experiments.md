# ADR-0037: Make the regression suite produce experiments, not run them

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Marcos Soto
- **Related:** [ADR-0028](0028-evaluation-modes.md),
  [ADR-0033](0033-experiment-above-run.md),
  [ADR-0034](0034-score-registry.md)

## Context

`benchmarks/<suite>/suite.yaml` already declares a measurement design: which cases
are canonical for validating a harness release, how many times each is worth
repeating, and which fields must be held constant. What it lacked was a way to
*execute* that design through the machinery every other comparison uses.

So validating a harness release ran down a parallel path with its own aggregation and
its own idea of what counted as a regression — while
[ADR-0033](0033-experiment-above-run.md) had already established that an experiment
is the unit above a run, and had already built design validation, arm expansion,
per-case repetitions and the fixed/broken/unchanged case matrix.

Two mechanisms, one of them worse, answering the same question.

## Decision

**The suite contributes the design; `ExperimentService` does everything else.**

`POST /api/experiments/from-suite` loads a suite, converts it into an ordinary
`ExperimentCreate` — its cases become `case_ids`, its per-case counts become
`case_repetitions` — and routes it through the same `create_experiment` path as a
hand-designed experiment. The resulting `Experiment` records `suite_id` so its origin
stays visible, and is a `harness_evaluation` experiment in every other respect.

The suite deliberately does **not** decide what the two arms are. It fixes what is
measured; the caller supplies the harness change being measured, and the suite's
`controlled_fields` are enforced against that choice — an arm that varies a field the
suite controls is refused rather than silently accepted.

There is no second runner.

## Consequences

Validating `Harness v0.6 → v0.7` is now the same object as every other comparison: it
gets design validation, the beyond-noise column, and above all the case matrix, so a
release is read as *"fixed four, broke two, left thirty unchanged"* rather than as a
single composite number that hides the two it broke.

The per-case repetition counts in `suite.yaml` survive into the experiment, which
matters because a suite deliberately repeats a cheap deterministic canary more often
than an expensive SWE-bench instance.

Two costs, accepted:

- **A suite can now be refused.** If the arms a caller supplies vary a field the
  suite controls, or vary nothing about the harness at all, creation fails with a 422.
  This is the point, but it does mean a suite invocation that "worked" before can now
  be rejected as an unanswerable design.
- **The suite file is no longer self-sufficient.** Running it requires a caller who
  names the control and the treatment. A suite alone cannot say what it is comparing,
  and pretending otherwise is what produced the parallel path.

## Alternatives considered

- **Teach the suite runner to aggregate like an experiment.** Rejected: that is
  reimplementing `ExperimentService` in a second place, with a second definition of
  regression that would drift.
- **Store the suite as a permanent experiment and re-launch it.** Rejected: each
  release comparison has different arms, so it is a different experiment. Reusing one
  record would overwrite the history of what each release actually did.
