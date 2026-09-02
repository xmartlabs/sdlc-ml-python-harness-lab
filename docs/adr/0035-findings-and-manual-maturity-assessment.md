# ADR-0035: Make a finding a testable claim, and keep the maturity assessment human

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Marcos Soto
- **Related:** [ADR-0028](0028-evaluation-modes.md),
  [ADR-0033](0033-experiment-above-run.md),
  [ADR-0034](0034-score-registry.md),
  [improvement-cycle.md](../improvement-cycle.md)

## Context

The improvement cycle already turns an audit into GitHub issues, but it points in one
direction: every proposal is routed to `TEMPLATE_REPO` or `HARNESS_REPO`, and the
prompt states that proposals target a doc or skill change *"not the subject task"*. It
improves **our** harness.

An engagement produces the other kind of output too — observations about how *this
team* works with agents. Those had nowhere to go, and the natural response is to write
them into a slide deck, where a claim and its evidence come apart immediately.

Two failure modes to design against. A recommendation asserted rather than tested is
just an opinion with a logo on it. And a maturity score computed by the lab from a
handful of runs would carry far more authority than the evidence behind it supports.

## Decision

We will introduce **`Finding`** as a testable claim, and **`Assessment`** as a
human-scored rubric.

A finding carries the observation, the **evidence** (run and case ids), a
**recommendation**, the **intervention** that would implement it, and the
**experiment** that settled it. Its status walks the consulting chain:

```text
Audit → Finding → Hypothesis → Experiment → Evidence → Recommendation
```

**A finding with no runs is labelled an observation, not blocked.** Noticing something
while reading a repository is a legitimate starting point; presenting it as evidence
is not. The label is the safeguard.

**A refuted finding keeps its record.** `refuted` is a first-class status with the
experiment id and outcome attached. This is the part a consultancy is tempted to drop,
and keeping it is most of what makes the rest credible.

**`experiment-draft` returns a draft, never a created experiment.** The design needs
cases and the harness axis the intervention actually changes, which a sentence does
not determine — and an experiment auto-generated from one would be exactly the
unanswerable design [ADR-0033](0033-experiment-above-run.md) refuses. The draft says
so in its own notes.

**The maturity assessment is scored by a human, against a published rubric** of seven
dimensions with five observable levels each, written as practice rather than sentiment
so two assessors reading the same evidence land close.

**A level above 2 requires evidence.** Levels 1-2 say a practice is absent or nominal,
which needs no backing. Anything higher claims a practice is *in place*, and that
claim must link the runs or findings that show it. An unscored dimension is reported
as unknown, never as zero.

## Consequences

The engagement chain closes: a run corpus produces findings, findings produce
experiments, experiments produce evidence, and the evidence updates the finding. What
is left behind is a regression suite and a record, not a deck.

Costs accepted:

- **A finding is manual to author.** Mining them from audits is possible later; doing
  it now would mean generated claims with no one accountable for them, which is the
  opposite of the point.
- **The rubric is one lab's opinion.** It is published, versioned in code, and
  therefore arguable — which is the most a maturity model can honestly be.
- **Nothing enforces that an assessment is re-scored when evidence changes.** It is a
  snapshot with a date and an author, and it is displayed as one.

## Alternatives considered

- **Compute a maturity score automatically from the run corpus.** Rejected: the corpus
  covers the tasks the lab happened to run, and generalising from it to "how this
  organization works" is precisely the overreach the modes in
  [ADR-0028](0028-evaluation-modes.md) exist to prevent.
- **Reuse `SavedProposal` for findings.** Rejected: a proposal targets a change to
  *our* harness and carries the repo it was authored against. A finding is about the
  client's practice and must be able to recommend a change to their repository, their
  process, or nothing at all.
- **Block a finding with no evidence.** Rejected: it would push the observation into a
  notebook, where it is neither labelled nor linked to the runs that later confirm it.
