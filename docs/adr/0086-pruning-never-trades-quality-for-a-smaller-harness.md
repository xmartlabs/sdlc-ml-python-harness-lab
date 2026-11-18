# ADR-0086: Pruning never trades quality for a smaller harness

- **Status:** Accepted
- **Date:** 2026-11-18
- **Deciders:** Marcos Soto
- **Amends:** [ADR-0076](0076-per-step-reward.md) (the removal rule in `pruning.py`)
- **Related:** [ADR-0085](0085-a-project-carries-a-skill-profile-not-the-whole-catalog.md)

## Context

The first two group-pruning experiments (2026-11-17) removed all 25 skills from
Haiku 4.5:

| Cases | Full harness | No skills | Verdict under ADR-0076 |
|---|---|---|---|
| 5 lab cases (`clinic-*`, `cli-textstats`, `eda-iris`), harness prompt | 10/10 | 10/10 | removable |
| 3 SWE-bench Pro ansible instances, plain prompt | 6/6 | 4/5 | inconclusive |

Both verdicts read the wrong way for what the harness is for.

- **A loss was tolerated.** The ADR-0076 rule was non-inferiority with a 5-point
  margin. It allowed a removal to cost up to 5 points of success, and called a
  20-point observed drop only *inconclusive*, because three cases give a wide
  interval. The harness exists to add quality on top of the bare model. A lower
  pass rate without it is the harness doing its job, not noise to be absorbed.
- **A ceiling was read as evidence.** When both arms pass everything, the cases
  cannot show what the skills add. "Removable" there meant "these cases are too
  easy", and nothing more.

## Decision

`assess_removal` now checks success in this order:

1. **Keep on any observed loss.** If the pooled pass rate over the paired cases is
   lower without the item, the verdict is `keep`, whatever the interval says. The
   interval measures how sure we are about a loss, not whether a loss is
   acceptable.
2. **No signal at the ceiling or the floor.** If both arms passed every scored run,
   or failed every one, the verdict is `no_signal`. It is never `removable`. The
   next step is harder cases.
3. **Removable only when earned.** With no observed loss and a signal, the existing
   ADR-0076 margins still apply: the success interval must stay above −5 points,
   and `cost_to_resolve_usd` and `progress_auc` must stay within 20% of the
   control. Fewer than three paired cases, a missing metric, or an interval that
   straddles a margin are still `inconclusive`.

Verdict order in the summary: keep, inconclusive, no_signal, removable.

## Consequences

- Re-read under this rule, the two experiments become **keep** (ansible:
  100% → 80%) and **no_signal** (lab cases at the ceiling). The Haiku `minimal`
  profile cannot drop skills on this evidence.
- Pruning becomes the exception that has to be earned. It needs discriminating
  cases, where some runs fail, plus enough of them. Expect more `no_signal` and
  `keep`, and fewer removals.
- A single unlucky run can now keep an item. That is the intended bias: a skill
  wrongly kept costs context; a skill wrongly removed costs quality. To overturn
  a `keep`, run more repetitions on the same cases. Never widen the margin.
- The goal becomes "add while staying balanced": refinement (`make refine-skill`)
  and profiles should raise the full-harness pass rate or cut its cost, never buy
  a smaller harness with lower quality.
