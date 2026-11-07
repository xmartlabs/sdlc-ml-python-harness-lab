# Measurement honesty: follow-ups after the first fixes (2026-11)

> **Status (2026-11-06):** items 1–5 are implemented on `fix/measurement-honesty`
> ([ADR-0073](../adr/0073-a-measurement-names-its-real-instrument-and-its-reach.md)).
> **Pick up items 6–10 once that branch is merged.** None needs a paid agent run to
> build. Item 6 can be measured on archived runs, and only item 9 spends model calls
> (a few repository audits, under US$1 each).

## Where this comes from

Federico Trinidad's review of the lab at `31d5f0e` ("Harness Lab bajo prueba",
2026-11-06) tested the statistics by Monte Carlo and read the audit and judge code.
We reproduced its numbers with the lab's own functions:

| Claim | Report | Reproduced |
|---|---|---|
| `plan_repetitions` with no data | 93 runs/arm | 93 |
| Sees a 20-pt move, 4×24, even effect | 70% | 71% |
| Sees it, 4×24, uneven effect | 32% | 34% |
| Sees it, 32×3, uneven effect | 66% | 67% |
| Delta CI covers the population, 4×24 uneven | 84% | 83% (effect on a random half of cases) |
| Wilson (one arm) covers the population rate, 4×24 | 58% | 55% |

The coverage drop depends on the effect landing on a *random* subset of cases. If the
same half is always affected, coverage is 97%. That is the case for reading results
as "tasks of this kind".

Federico also wrote `tribunal` (`xmartlabs/agent-skills`, `plugins/tribunal`), a PR
reviewer whose `tribunal compare` reached the same paired cluster bootstrap on its own.
Several items below borrow from it.

## Done on `fix/measurement-honesty` (items 1–5)

1. The effective model (from `modelUsage`) and temperature (`None` on the CLI) are
   recorded by the DeepEval judge and the repository audit.
2. A `few_cases` validity issue appears below 5 paired cases.
3. Plugin skills and agents (`plugins/<p>/…`) count as harness everywhere.
4. The planner docstring and the factorial reading (with its panel copy) are corrected.
5. Judgement aggregates are split by judge model.

## Next, in order

### 6. Measure the judge against the checks (small to medium)

`task_completion` (judge) and `task_validation_pass` (checks) are scored on the same
runs, and nothing compares them. Two cheap readings over stored scores, with no new
runs:

- **Agreement:** a confusion table of `task_completion >= 0.5` against
  `task_validation_pass`, per judge model (now possible: aggregates carry
  `judge_model`). Report Cohen's κ only past ~20 runs.
- **Exclusions per arm:** how many runs the judge skipped (`MetricOutcome.skipped_reason`
  never becomes a `Score` row), per arm of each experiment. If it skips more on long
  trajectories, the mean leans toward short ones.

Where: a `ScoreService.judge_agreement(run_ids)` method, plus a line on the experiment
page next to the judged deltas.

### 7. Show the Wilson interval as "on these cases" (small)

`ConditionSummary.success_rate_ci` is a Wilson interval over every run as if
independent. It is right for "how did it do on these cases?" and covered the population
rate only 55–58% of the time at 4×24. Relabel it in `Experiments.tsx` (EN/ES), and add
a case-clustered interval beside it: the same two-stage bootstrap
`paired_bootstrap_interval` uses, on one arm.

### 8. Plan repetitions by simulation (medium, the most valuable)

Replace the two-proportion formula in `plan_repetitions` with a simulation of the
design. Draw per-case rates from the observed ones (or 10–90% uniform with no data),
apply a 20-point effect even and uneven, and compute the interval exactly as
`success_delta` reports it. Answer two questions: "how many runs?" and "more cases or
more repetitions?". With an uneven effect the answer is almost always cases. Extend it
to `factorial.analyze`: how many runs per cell to see a 30-point interaction (today
48 per cell sees it under 40% of the time). Keep it deterministic (fixed seed) and
cheap (~200 draws × 500 resamples). `statistics.py` already holds every building block.

### 9. Repeat the repository audit and merge its cases (medium)

Five audits of one repo agreed on the verdict every time. They agreed on ~83% of
components, and one audit proposed 4.4 of the 6 case themes that appeared across all
five. Two audits cover 5.5 and three cover 5.9, for under US$1. Add an `audits=N`
option (default 1) that runs the map and case passes N times. It keeps the union of
cases, deduplicated by title similarity, and marks each with how many audits proposed
it. Store `audit_samples` on the row, and keep N>1 audits out of single-audit
comparisons, as tribunal does with `lensSamples > 1`. Also report how many map entries
were dropped for having no usable name (4 of 5 audits dropped one silently).

### 10. Cross `skill_adherence` items against the catalog (small to medium)

`skill_adherence = useful / len(items)` over whatever list the auditor returned
(`evaluation.py`, `HarnessAuditEvaluator`). An omitted, invented or duplicated item
moves the number without notice. Match items against the run's harness catalog
(`harness_catalog_from_metadata`). Then compute adherence over the catalog: an
unjudged item is unknown, not useful. Record `unmatched`, `missing` and `duplicates`
in provenance. Bump the score to `v2`, since the denominator changes.

## Also open (smaller, found while doing 1–5)

- **`rubric_score` records its model only in free-text notes**
  (`tasks._llm_judge_rubric`). Route `_worker_gateway` through `chat_reply`, and put
  `model` / `effective_model` in the score's provenance so item 5 can split it.
- **The run auditor (`scripts/bench_audit.py`) records the requested model**
  (`Auditor model:` header). Use `chat_reply` there as well.
- **Warn when the judge shares a family with the agent.** A CLI-only lab has Claude
  judging Claude, and nothing says so.
- **Self-audit invariants per run**, after tribunal's `selfAudit`: named checks on the
  stored row (judge excluded the run, map entries dropped, adherence items unmatched),
  with a non-failing `notes[]` channel for checks whose precision is not established.
- **The gateway path records the request as the effective configuration.** LiteLLM
  model names are aliases, and with `drop_params: true`
  (`docs/local-model-runtime-config.md`) a parameter the backend rejects (some
  reasoning models accept only temperature 1) is dropped without notice. Record the
  `model` field of the gateway's own response in `ChatReply.model`. Verify on the real
  gateway whether a judge's `temperature=0.0` reaches the backend. If not, record it as
  unknown, as on the CLI.
- **`few_cases` and the factorial grid disagree on the threshold.** `factorial` calls
  an interaction "established" from 3 cases (`MIN_CASES_TO_ESTABLISH`), while validity
  warns below 5. `harness_by_model` experiments carry no `success`, so `few_cases`
  never fires there. Validity also reads only the first comparison of a multi-arm
  experiment. Decide on one threshold and apply it to every comparison.

## Offered to `tribunal` (for Federico)

Measured by replicating `compare.rs` (6 items, 6 runs per arm, 250 simulations):
1 PR gives 6–9% false positives (nominal 5%) and sees a ~14-point recall gain 21%
of the time. 5 PRs give 4% and 31%. Suggestions: a Newcombe-style floor when fewer
than 5 units are matched, the pass@k estimator to project `--lens-samples` recall from
existing runs, and a shared simulation-based power check. The
`{unit, run, item, found}` format also describes a lab experiment.
