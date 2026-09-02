# ADR-0034: Record every metric against a declared, versioned definition

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Marcos Soto
- **Related:** [ADR-0004](0004-objective-validation-and-multidimensional-score.md),
  [ADR-0005](0005-structured-harness-attribution.md),
  [ADR-0008](0008-llm-audit-in-web.md),
  [ADR-0033](0033-experiment-above-run.md)

## Context

[ADR-0004](0004-objective-validation-and-multidimensional-score.md) already refuses to
collapse a run into one number: `Validation` keeps objective, cost, effort and
subjective apart. What it stores in each is free-form JSON, which leaves three
failure modes — all of them silent.

**A metric can change meaning under a stable name.** Change the auditor model, the
rubric, or the formula, and `skill_adherence` before and after are different
quantities. Pooled into a trend line, the change *is* the trend.

**A fact and an opinion look identical once both are numbers.** `test_pass_rate = 1.0`
is a command's exit status. `skill_usefulness = 0.81` is a model's view. Rendered in
the same table with the same precision, the second borrows the authority of the first.

**A number's direction lives only in the reader's head.** Nothing says whether more
`agent_steps` is better, so a comparison surface can render an improvement as a
regression and be believed.

## Decision

Every metric the lab records will be declared in a **registry** — in code, in
`scores.py` — and every stored value filed under `name@version`.

A definition carries `category` (outcome / efficiency / trajectory / behavioral),
`kind`, `direction`, `version`, a description, and **`provenance`**, which is one of:

- **`fact`** — a command exited, a file exists. Reproducible without judgement.
- **`observation`** — parsed from artifacts. True of the record, not necessarily of
  the world: `governance_read_coverage` is a lower bound because context-injected
  content leaves no read event.
- **`judgement`** — a model's opinion. Defensible, not checkable.

**Recording an undeclared score raises.** An undeclared metric has no direction, no
provenance and no version, so nothing downstream can read it correctly; failing at
write time is the only point where that is cheap to fix.

**The registry is code, not data.** It is reviewed, versioned and diffed with the
change that motivated it. A registry stored in the database would let a metric's
meaning drift with no diff and no review — which is the failure mode this ADR exists
to prevent.

**Evaluators are separable, one per level.** `DeterministicEvaluator`,
`EfficiencyEvaluator` and `HarnessBehaviorEvaluator` each own one level and read a
prepared `EvaluationContext`. An **evaluation profile** is a named set of them, so two
arms of an experiment are always scored by the same instruments — and so DeepEval can
later be one more adapter rather than a platform the lab is rebuilt around.

**Re-scoring overwrites in place.** Two values for one `(run, name, version)` would be
a contradiction, not a history. A genuine change of method takes a new `version`.

**An absent input emits nothing, never zero.** A run with no `usage.json` has unknown
cost; recording `0` would be a claim, and one that flatters the run.

## Consequences

Aggregation and comparison (later phases) can group by a stable key, respect
`direction` without a lookup table, and refuse to average a judgement with a fact.

The UI gains an obligation: render the three provenances differently. That is the
whole return on this ADR, and it is not delivered by the ADR — a UI that draws them
identically leaves the lab exactly where it was.

Costs accepted:

- **Adding a metric is a code change with a review.** Deliberate friction: it is the
  moment to decide direction and provenance, which nobody will do later.
- **`Validation` and `Score` now overlap.** `Validation` remains the run-shaped
  record ADR-0004 defined; `Score` is the comparable, registry-backed form.
  Collapsing them is possible later; doing it now would mean migrating the
  historical corpus before the new shape has been used in anger.
- **The behavioural evaluator parses free-text verdicts.** The auditor writes Spanish
  ("útil", "redundante"), so matching folds accents. It is a heuristic over prose, and
  the honest fix is an auditor that emits the structure directly — tracked, not done.

## Alternatives considered

- **Keep free-form JSON in `Validation`.** Rejected: it is exactly what makes the
  three failures above invisible.
- **A `ScoreDefinition` database table.** Rejected: a definition edited in production
  changes the meaning of history with no diff and no review.
- **Adopt an external metric taxonomy (DeepEval's, Langfuse's).** Rejected as the
  source of truth: neither has a concept of harness version, skill ablation, target
  repository or sandbox fingerprint. They are welcome as *sources* of individual
  scores, which is what `source` on a definition records.
