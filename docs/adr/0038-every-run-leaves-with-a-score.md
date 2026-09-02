# ADR-0038: Every run leaves with a score, and the arms it is compared against must be commensurable

- **Status:** Accepted
- **Date:** 2026-09-02
- **Deciders:** Marcos Soto
- **Related:** [ADR-0004](0004-objective-validation-and-multidimensional-score.md),
  [ADR-0026](0026-run-continuation-as-child-runs.md),
  [ADR-0031](0031-harness-source-is-a-run-condition.md),
  [ADR-0033](0033-experiment-above-run.md),
  [ADR-0034](0034-score-registry.md)

## Context

The first real corpus (twelve Tetris runs across two arms) produced a comparison
that looked complete and was not readable. Five separate mechanisms conspired,
and each of them was individually defensible.

**Runs left their arm silently.** "Run again" cloned a run's configuration with
care — every model, prompt, ablation and target axis — and dropped
`experiment_id` and `condition_label`, because those live on `Run` rather than on
`RunConfig`. The four repetitions an operator started after seeing an error
executed the experiment's design exactly and counted towards nothing. Nothing in
the UI said so: an unattributed run and an attributed one are drawn identically.

**Most runs never got a score at all.** Only SWE-bench cases auto-scored; every
other case parked in `awaiting_manual_score` waiting for a human who, in
practice, scores the first two runs and never returns. An experiment can only
compare what has been scored, so the comparison fell back to
`task_validation_pass` — for the pilot case, a smoke check asking "does it
start". That metric cannot fall once both arms start, and it reported 100% → 100%
as *no difference observed*.

**The one metric that did move was measuring the prompt, not the agent.** The
runner's outer loop stopped early on a prose sentinel ("all milestones
complete") that the `sdlc_full` prompt teaches the agent to emit and the `naive`
prompt does not. The bare arm was structurally unable to terminate early and
always burned its full budget. `agent_steps 10 → 1` — and the cost, token and
wall-time deltas that inherit it — was a property of prompt authorship reported
as a treatment effect.

**Three "beyond noise: yes" verdicts rested on no observed noise.** With every
repetition of both arms landing on the same value, the noise floor is exactly
`0.0` and any non-zero delta clears it. Coarse metrics at low repetition counts
hit this constantly, and it is the most confident-looking wrong answer the
comparison can produce.

**Three runs were filed under a harness that never executed them.** A
pre-declared `runs.yaml` cell out-voted the explicit `--harness` flag, so the
stored config said `claude` while `metadata.json` — the file the corpus, the
leaderboard and the improvement cycle all read — said `opencode`.

The common shape: every one of these is invisible at the point where someone
reads the result. A comparison whose arms are not commensurable does not look
broken. It looks like a finding.

## Decision

**Attribution is carried, not re-offered.** `clone_run` writes the source run's
`experiment_id` and `condition_label` in the same transaction as the row. The
configuration is identical by construction, so the arm is not a choice to
re-make. Continuation (ADR-0026) deliberately does *not* inherit it: a
continuation is a human intervention, and counting it as another repetition
would score the operator's hint as if the agent had earned it.

**Every case that carries a scale gets scored automatically.** Two scales exist,
and which applies is a property of the case: an objective verdict where one
exists (SWE-bench's resolved/not-resolved *is* the score, and no model opinion is
invited on top of a fact), and otherwise the case's own written
`scoring_rubric`, graded by the audit LLM. A judged rubric score is an opinion
and is recorded as one — provenance `judgement`, source `llm_judge` (ADR-0034) —
and a human can overwrite it. A case with neither a rubric nor an objective
verdict is the only one that still waits for a person.

**The stop criterion is arm-neutral, and every run records why it stopped.** The
no-progress fingerprint check applies to every lane and is evaluated before the
prose sentinel, so no arm is structurally denied early termination. Runs record
`stop_reason` (`swe_resolved`, `no_progress`, `completion_signal`,
`budget_exhausted`, `harness_error`), and a comparison whose arms stopped for
disjoint reasons carries an automatic caveat.

**`agent_steps` is not a work metric.** It counts harness restarts: one
invocation that ran fifty turns counts 1. `agent_turns` and `agent_tool_calls`
are recorded alongside it and are what "how much did the agent do" should read.

**No spread means unknown, not yes.** `exceeds_spread` is `None` — not `True` —
when neither arm has more than one repetition, or when the observed spread is
exactly zero.

**An explicit flag beats a stored cell, and a divergence is announced.**
`--harness` wins over `runs.yaml`, and when the two disagree the runner says so.
After the run, the lab compares `RunConfig.harness` against what `metadata.json`
records and writes a provenance mismatch onto the run.

## Consequences

The lab now produces scored runs by default, which is what makes a comparison
possible at all. It also means most scores in the corpus are a model's opinion,
and the burden moves onto the UI to keep drawing that difference (ADR-0034's
unpaid debt, now larger).

Verdicts get quieter. Removing the zero-noise-floor "yes" and adding the
stop-reason caveat both take confidence away from the screen without adding any.
That is the intended direction: an experiment that cannot support a claim should
be visibly unable to, and a caveat nobody reads still beats a number nobody
should have believed.

Historical runs are not retro-fitted. The runs that predate this ADR keep their
gaps; re-validating one now auto-scores it, but the arms it belongs to were
already measured under the old stop criterion and cannot be repaired by
re-reading them. The corpus this ADR describes should be re-run, not corrected.

Costs accepted:

- **An LLM-judged rubric score is not reproducible.** Two runs of the judge can
  disagree, and a rubric change silently changes the quantity. ADR-0034's
  `name@version` is the mitigation, and it is a mitigation, not a fix.
- **`stop_reason` is a coarse taxonomy.** "Budget exhausted" covers an agent
  still working and an agent stuck in a loop; the fingerprint check separates
  many but not all of them.
- **The provenance mismatch check is a warning, not a refusal.** A mismatched run
  still enters the corpus. Refusing it would discard the only evidence that the
  divergence happened.

## Alternatives considered

- **Backfill the orphan runs into their experiment.** Rejected: they ran under
  the arm-dependent stop criterion, so attributing them would import the
  confound rather than recover the repetitions.
- **Let the judge score without a rubric.** Rejected: a model asked to invent a
  scale will produce a number, and a number with no declared scale is exactly
  what ADR-0034 exists to prevent.
- **Compute a p-value instead of `exceeds_spread`.** Rejected for now, on the
  same grounds as ADR-0003: at these repetition counts a test would be a more
  authoritative way of saying the same unreliable thing.
- **Keep `runs.yaml` authoritative for the harness.** Rejected: a stored cell
  describing a run it did not launch is the failure this ADR is fixing.
