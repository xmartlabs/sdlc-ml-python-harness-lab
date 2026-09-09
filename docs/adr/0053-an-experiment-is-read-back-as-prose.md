# ADR-0053: An experiment is read back as prose

- **Status:** Accepted
- **Date:** 2026-09-09
- **Deciders:** Marcos Soto
- **Related:** [ADR-0036](0036-audit-judges-a-run-against-its-own-governance.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  [ADR-0046](0046-an-experiment-reports-validity-before-it-reports-a-verdict.md),
  [ADR-0051](0051-the-harness-ladder-is-one-family-of-single-factor-experiments.md)

## Context

The experiment page computes everything a reader needs — validity, verdict,
highlights with direction, the deltas table, per-arm skill usage and governance
coverage — and renders it as cards, badges and caveat bullets. Reading the first
valid bare-vs-harness experiment still took a person twenty minutes across four
run pages to say the four sentences that mattered: both arms resolved, the harness
cost 2.6×, it read four governance docs, and it invoked none of the twenty-five
skills it was asked to. Every one of those facts was on the page; none of them was
*said*.

A run already has this: the LLM audit reads a run's artifacts and writes a
diagnostic (ADR-0036). An experiment did not.

## Decision

**A finished experiment can be read back by an LLM, on demand, into six fixed
sections:** verdict, what was compared, result, what was used and what was not,
why the comparison can (not) be trusted, what to do now. The model is given one
evidence document assembled entirely from what the lab already computed —
`summarize()` (validity, verdict, deltas, case matrix), and per run the objective
summary with its failed checks, the judge's level and rationale, the registry
scores, and the attribution split into *read / not read* governance documents and
*invoked / asked-for-but-not-invoked / available-but-never-opened* skills. The
evidence is persisted beside the text (`ExperimentAudit.evidence_json`) so any
sentence can be checked against the number it came from.

**The reading waits for the last run.** `POST /api/experiments/{id}/audit` returns
409 while any run is still executing, and the button is disabled until then. A
reading of half an experiment would describe a comparison that does not exist yet
and be persisted as though it did. "Finished" is derived from the runs' states,
not from `Experiment.status`, which is set at launch and lags.

**The prompt bans what the numbers ban.** No "significant", no causes the evidence
does not contain, numbers copied verbatim, the repetition tier named in the lab's
own words (exploratory / judgement / validation), and an invalid comparison stated
first. It writes in the language of the run audit.

## Consequences

The page gains one card, placed under the verdict, and one gateway call per
reading. The cards stay: the reading is a layer over them, not a replacement, and
it is labelled by the model that wrote it. A reading that fails is recorded as
failed with its reason, never raised — an experiment without prose is still an
experiment.
