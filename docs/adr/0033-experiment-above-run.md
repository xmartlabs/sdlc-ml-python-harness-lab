# ADR-0033: Put `Experiment` above `Run`, and refuse a design its mode cannot answer

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Marcos Soto
- **Related:** [ADR-0023](0023-batch-run-execution.md),
  [ADR-0028](0028-evaluation-modes.md),
  [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [ADR-0031](0031-harness-source-is-a-run-condition.md)

## Context

`POST /api/runs/batch` already produces N runs from a cross-product, capped and
preflighted. What it cannot do is say what those runs are *for*.

Two gaps follow from that, and both are about authority rather than convenience.

An arm could only be **recovered**, never declared: `Compare.tsx` reconstructs a run's
case, prompt and models by parsing the runner **command string**. That works, and it
means the experimental design exists nowhere until after the runs have executed.

And nothing could reject an unanswerable design. A batch varying both the harness and
the target repository is a valid request and an invalid experiment — whatever
difference it produces cannot be attributed to either factor. Today the lab would
queue it, then render an average, and the average would look exactly like a result.

## Decision

We will introduce **`Experiment`** as the entity above `Run`, carrying a required
`question`, a `mode` ([ADR-0028](0028-evaluation-modes.md)), the shared cases and
prompt variants, and a repetition count. **`ExperimentCondition`** declares each arm
up front: a label, a `control`/`treatment` role, and the axis values that arm fixes.
`Run` gains a nullable `experiment_id` and `condition_label`.

**The question is required.** An experiment nobody can state the question for is a
batch, and calling it an experiment lends it authority it has not earned.

**The design is validated against the mode, and an unanswerable one is rejected at
creation.** This is where ADR-0028 stops being documentation:

- `harness_evaluation` refuses conditions that also vary the repository or the
  executor model, and refuses conditions that vary *nothing* about the harness;
  varying more than one harness factor at once warns rather than blocks.
- `cross_repo` requires the repository to vary and refuses anything else varying
  with it, and always returns the warning that scores from different repositories
  must be read per repository, never pooled.
- `discovery` validates nothing, because it claims nothing. Its safeguard is the
  label on its output.
- Any comparison needs **exactly one control** — the arm the others are read against.

**Repetition counts state what they do not support.** N=1 returns "exploratory only";
N<5 returns "exploratory comparison". Warnings, not blocks: a quick look is a
legitimate thing to want, and mislabelling it is not.

**Launching reuses the batch machinery** — validate everything before persisting
anything, preflight deduplicated by what it reads, one `git ls-remote` per
(repository, ref) so every repetition of an arm carries the same commit.

## Consequences

The canonical consulting experiment becomes a first-class object: *"does our harness
improve endpoint work on their repo?"*, control `native`, treatment `injected`, their
cases, N repetitions — declared, validated, then executed.

Aggregation, comparison surfaces and findings (later phases) get a stable grouping key
that means something, instead of `batch_id`, which only means "created together".

Costs accepted:

- **Two ways to create runs now exist.** Batch stays: an ad-hoc sweep is a real need
  and forcing a question onto it would only produce questions nobody means. The
  roadmap's intent is that `suite.yaml` becomes a producer of *experiments*, and
  batch remains the unceremonious path.
- **The validity rules are opinions in code.** They encode this lab's methodology, and
  a user with a defensible reason to violate one has no override. That is deliberate
  for now — an escape hatch added before anyone has hit the wall would be used to
  route around the rules rather than to fix them.
- **Historical runs have no experiment.** They are shown as ungrouped rather than
  back-filled: inventing membership would place runs into a design they were never
  part of, which is the same fabrication `batch_id` and `parent_run_id` refused.

## Alternatives considered

- **Grow `batch_id` into an experiment.** Rejected: a batch is a creation event and an
  experiment is a claim. Overloading one field to mean both would make "was this a
  designed comparison?" unanswerable for exactly the records where it matters.
- **Keep deriving arms from the run command.** Rejected: it makes the design a
  reconstruction, so it cannot be validated *before* spending model budget on it —
  which is the only moment when rejecting it is cheap.
- **Validate the design only in the UI.** Rejected: the API is the contract, and a
  rule enforced in one client is a rule that a script silently skips.
