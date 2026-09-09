# ADR-0051: The harness ladder is one family of single-factor experiments

- **Status:** Accepted
- **Date:** 2026-09-08
- **Deciders:** Marcos Soto
- **Related:** [ADR-0028](0028-evaluation-modes.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  [ADR-0044](0044-a-single-prompt-case-can-carry-the-full-harness-pair.md),
  [ADR-0046](0046-an-experiment-reports-validity-before-it-reports-a-verdict.md),
  [ADR-0049](0049-a-bare-arm-is-a-masked-runner-not-a-lab-root-run.md)

## Context

"Does the harness help, and by how much?" is the lab's founding question, and a single
two-arm experiment cannot answer the second half. The harness is four things — the
governance body (CLAUDE.md, hooks, subagents, `.github/`), the skill catalog, the
prompt preamble that directs the agent through them, and the activation block that
asks for the skills outright — and `full_harness` moves all four at once. The wizard
says so (advisory C), and `validate_design` would call any two of them varying together
a confound. Correct, and it means the "how much" has to be built from single-factor
steps.

## Decision

**The ladder is four experiments, each moving one factor on top of the last:**

| rung | control → treatment | wizard factor |
|---|---|---|
| 1 | bare → governance-only (skills hidden, plain prompt) | Governance body |
| 2 | governance-only → skills available | Remove skills, read in reverse |
| 3 | skills available → harness prompt | **Harness prompt** (new) |
| 4 | harness prompt → activation block | **Activation** (new) |

Every rung is an ordinary `harness_evaluation` experiment. `scripts/ladder.py`
(`make ladder`) creates the four consistently — same cases, same model, same
repetitions, each rung's control byte-identical to the previous rung's treatment —
and tags them with one `Experiment.family_id`. The results page filters by family.

**A family is one (harness CLI, model) pair.** Harness value is a function of the
model: what Haiku does with a rulebook and what a local Qwen does with it are two
measurements. The family id carries both, and rungs are never compared across pairs.

**A rung is exempt from the partial-ablation objection.** ADR-0046 added a warning for
a skills-only experiment inside the lab, because a reader could mistake it for a
harness comparison. A rung of a family cannot be so mistaken — it is single-factor by
design, read beside the rung that measured the governance body — so `validate_design`
skips that warning when `family_id` is set. The same warning is also no longer raised
when only `skills_invoke` varies: no directory differs between those arms.

**Rung 2 will report `treatment_not_activated` when the agent opens no skill.** That
is not a broken experiment; it is the finding of that rung — availability is not use
(ADR-0041) — and the reason rung 4 exists.

## Consequences

A ladder on two cases at five repetitions costs about 40 runs per pair — on Haiku,
roughly $20 and two and a half hours of agent time. Each rung reports its own validity,
so a family can be read as "governance helped, skills did nothing until asked, the
prompt cost tokens for no gain" instead of one confounded number.

A `Ladder` view that renders cumulative deltas per rung is deferred until rung data
exists; the family filter on the experiments list is enough to read the four verdicts
side by side.
