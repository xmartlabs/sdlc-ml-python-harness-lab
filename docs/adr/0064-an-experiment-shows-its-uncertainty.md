# ADR-0064: An experiment shows its uncertainty

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** Marcos Soto
- **Related:** [ADR-0003](0003-trial-provenance-and-harness-fingerprint.md),
  [ADR-0046](0046-an-experiment-reports-validity-before-it-reports-a-verdict.md),
  [docs/plans/2026-10-harness-ecosystem-analysis-and-improvement-plan.md](../plans/2026-10-harness-ecosystem-analysis-and-improvement-plan.md)
  (finding I3, step 0.3)

## Context

`statistics.py` has had a `confidence_interval()` since Phase 8 of the lab roadmap,
and only its own test ever called it. No interval reached the API or the page. The
experiment page labelled five repetitions a **validation**, and printed "60% → 100%"
beside it, when the 95% interval of 3 of 5 runs from 23% to 88%. The wording ladder
(exploratory, judgement, validation) said that five runs were *more* than two. It did
not say how wide five runs still were, and readers took "validation" for a settled
result.

Three gaps sat behind that:

1. **No interval on a pass rate.** The interval that existed was a normal
   approximation over a metric's values. For a pass rate it is wrong exactly where
   this lab works: at 0 of 5 or 5 of 5 it collapses to `[0, 0]` or `[1, 1]` and claims
   certainty from five runs.
2. **No interval on the success delta.** This is the number the verdict leads with.
   The runs behind it are not independent. Repetitions of one case share that case's
   difficulty, so treating forty runs over four cases as forty facts gives an interval
   far narrower than the design supports.
3. **One reduction per case.** The case matrix reduces a cell with `all(reps)`. That
   is correct for "did the treatment fix it reliably", but it hides how often a case
   passes, and there was no pass@k or pass^k beside it.

The lab's position on significance has not changed: it runs no hypothesis test and
never says *significant*.

## Decision

**Every arm carries a 95% interval on its success rate.** `wilson_interval()`
computes the Wilson score interval, and `ConditionSummary.success_rate_ci` exposes it
over the same scored-run denominator as the rate. The summary carries
`success_rate_ci_low` / `success_rate_ci_high` per condition, and both are `null` when
nothing was scored. Wilson stays honest at 0/n and n/n: 0 of 5 is `[0%, 43%]`.

**The success delta carries a case-clustered interval.** `success_delta()` now
returns `delta_ci_low` / `delta_ci_high` in points, `interval_crosses_zero` and
`paired_cases`. The interval is the envelope of two intervals:

- **A paired, two-stage bootstrap** (`paired_bootstrap_interval`). Each resample draws
  cases with replacement, using the same draw for both arms so the comparison stays
  paired, then draws each arm's repetitions within each drawn case. Only cases that
  both arms scored take part. The seed is fixed (`BOOTSTRAP_SEED = 64`, 2,000
  resamples), so the same runs always produce the same interval. Pure Python: numpy
  is only a dev dependency, and the API image installs with `--no-dev`.
- **Newcombe's hybrid score interval**, built from the two arms' Wilson intervals.

The envelope exists because the bootstrap has a blind spot. When every repetition
agrees (0/5 against 5/5), it has nothing to resample and returns `[+100, +100]`.
Newcombe's interval does not collapse there (`[+39, +100]`), and it also covers
callers that only hold arm totals. The reported interval is therefore never narrower
than Newcombe's, and it widens to the bootstrap when case clustering makes the data
noisier than the pooled view suggests.

**pass@k and pass^k per arm.** `pass_at_k()` reports both, averaged over cases with
equal weight. `k` is the fewest scored repetitions any case has in *any* arm
(`pass_k_by_arm`), because pass^3 beside pass^2 would answer two different questions.
A case with more runs than `k` uses the unbiased estimator over every k-subset, not
its first k runs. pass^k (all k tries pass) is the reliability number. pass@k is shown
beside it, and the gap between them shows how much retrying hides.

**A planning hint, offered as advice.** `plan_repetitions()` answers one fixed
question: how many repetitions per case per arm would detect a 20-point difference at
a two-sided 5% level with 80% power. It uses the standard two-proportion formula. It
plans from a 50% rate until every arm has 10 scored runs, and after that from the
observed arm closest to 50%. It ignores what pairing saves, so its estimate errs high.
The summary exposes it as `repetition_plan`, plus the flat
`repetitions_needed_for_20pp`, and only for designs that have a comparison. The page
labels it "advice for the next design, not a judgement on this one".

**Intervals describe; they never gate.** The verdict's headline is still chosen by
the repetition ladder and the 5-point materiality floor. An interval is added to the
claim ("+40 points (95% interval -12 to +80)"). When it includes zero, a caveat says
"these runs are also compatible with no effect", or for a tie "a tie this wide does not
show the arms are equivalent". Using the interval to pick the headline would make it
a significance test by another name, and this lab does not run those.

## Consequences

- A reader sees "60% [23–88]" instead of "60%". At five repetitions the interval is
  about ±35 points wide, and the page now shows it.
- The case-clustered interval is honest about designs with few cases and many
  repetitions. Those designs now show wider intervals than before, which is the
  intended effect.
- The summary payload gains fields and changes none. `success` gains four keys, each
  condition gains three, and the summary gains two. Consumers that read known keys
  (the audit evidence, the repository report) are unaffected.
- The bootstrap runs on every summary request. It takes about 7 ms for 5 cases × 5
  repetitions and about 120 ms for 60 × 10, measured locally. That is acceptable for an
  on-demand page. If experiments grow by an order of magnitude, the interval should be
  cached by run set.
- `interval_crosses_zero` is a convenience for the page. Nothing in the backend
  branches on it except the caveat text.

## Alternatives considered

- **Bootstrap alone.** Rejected because of the unanimous-arm collapse described
  above. A lab whose arms often sit at 0% or 100% cannot report "+100, certainly"
  from ten runs.
- **Newcombe alone.** Rejected because it treats every run as independent and ignores
  case clustering, which is the dependence that matters most in a multi-case design.
- **A p-value or a "significant" badge.** Rejected, as in Phase 8 of the roadmap.
  Intervals carry the same information without the binary framing that invites
  misreading at N = 5.
- **Letting the interval pick the headline** (for example "Likely improvement" only
  when the interval clears zero). Rejected, because it is a significance test in
  disguise. It may be revisited once A/A experiments have set the lab's noise floor.
- **numpy for the resampling.** Rejected because numpy is only a dev dependency. The
  pure-Python loop is fast enough at the lab's sizes and adds nothing to the API image.
