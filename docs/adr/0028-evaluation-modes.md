# ADR-0028: Make the evaluation mode an explicit property of a comparison

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Marcos Soto
- **Related:** [ADR-0003](0003-trial-provenance-and-harness-fingerprint.md),
  [ADR-0023](0023-batch-run-execution.md),
  [roadmap-lab-v2.md](../roadmap-lab-v2.md)

## Context

The lab can already hold many factors constant and vary one: `condition_hash`
identifies an arm, and `POST /api/runs/batch` generates a cross-product over cases,
prompts, harnesses and models. What it cannot do is say **what kind of question a
given set of runs answers**, and therefore what may legitimately be concluded from
comparing them.

Three uses of the lab have appeared, and they license different conclusions:

- vary the harness on a fixed repo and case (a causal question);
- vary the repository with the harness fixed (a robustness question);
- observe how a team already works, with no control at all (an exploratory question).

Nothing in the current model distinguishes them. A cross-repo batch and a harness
A/B are the same shape of data, so the UI would happily render a mean across
repositories as though it were a controlled result. That is not a display bug; it is
a conclusion the design does not support, presented as though it did.

## Decision

We will make the **mode** a declared property of a comparison, with three values.

**`harness_evaluation` (Mode A)** — target repository, case, model and environment
are held fixed; the harness varies (skill, rule, prompt, subagent, workflow,
version, or `native` vs `injected`). This is the only mode that licenses a causal
claim, and only with ablation plus repetitions.

**`cross_repo` (Mode B)** — harness, skills, model and task family are held fixed;
the target repository varies. Scores from different repositories are **not**
comparable as if conditions were identical. The mode licenses statements about
where a harness holds and where it degrades, never a pooled average across repos.

**`discovery` (Mode C)** — no control. Output is a report: practices observed,
missing or redundant skills, conventions worth porting, candidate cases and
experiments. Every artifact carries the label **exploratory evidence, not
experimental evidence**.

The mode is recorded, not inferred. Where the data model cannot yet carry it (there
is no `Experiment` entity until a later phase), the mode is carried by the artifact
that groups runs, and the UI must degrade to "unlabelled comparison" rather than
guess.

## Consequences

The surfaces that aggregate runs gain an obligation: **refuse, or visibly qualify,
a comparison the mode does not support.** Pooling a mean across target repositories
must be blocked in Mode B rather than merely discouraged in documentation.

Discovery output becomes a first-class artifact rather than a weaker experiment.
Labelling it exploratory is what makes it safe to produce quickly — the label is a
methodological guard, not a disclaimer.

Cost accepted: three modes is three sets of validity rules to implement and keep
honest, and a batch created before this ADR has no mode. Those are shown as
unlabelled rather than back-filled with a guess — inventing a mode for historical
runs would fabricate the very thing this ADR exists to record.

## Alternatives considered

- **Leave the mode implicit in how the user configures a batch.** Rejected: the
  configuration is ambiguous by construction. A batch varying both harness and
  repository is a valid request and an invalid experiment, and nothing in the data
  distinguishes the two.
- **Only support Mode A, the rigorous one.** Rejected: cross-repo robustness and
  discovery are the questions an external engagement actually starts with. Refusing
  to model them does not stop them being asked; it just means they get answered
  with Mode A's vocabulary and Mode A's unearned authority.
