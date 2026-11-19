# ADR-0089: A profile grows one trialled skill at a time

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Marcos Soto
- **Related:** [ADR-0085](0085-a-project-carries-a-skill-profile-not-the-whole-catalog.md),
  [ADR-0086](0086-pruning-never-trades-quality-for-a-smaller-harness.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  [ADR-0087](0087-a-case-can-ship-the-candidate-skill-it-trials.md)

## Context

`minimal` (ADR-0085) was chosen from a usage review, not from experiments. The lab
could already ask three questions about it:

- whether skills can leave (`make prune`);
- whether a rewrite beats the original (`make refine-skill`);
- whether a profile matches `full` (`make skill-profile-experiment`).

It could not ask the question a small default profile needs most: **should this one
catalog skill join it?** New skills keep arriving through ordinary PRs
(`sensible_testing` in #52), and each one lands in `full` without evidence.

The design an engineer would write by hand changes two fields: the treatment
re-enables the skill (`skills_disabled` loses it) and asks for it (`skills_invoke`
gains it, per ADR-0041). The validator read that as two harness factors and warned of
a confound on the one design that is not confounded. It already folded the staged
equivalent (`skills_extra` + `skills_invoke`) into one intervention.

## Decision

- **Add `make add-skill-trial`** (`scripts/add_skill_trial.py`). Mode is
  `harness_evaluation`. The control is the base profile (`minimal` by default), and
  the treatment is the same profile plus one governed skill, asked for. It refuses a
  skill the profile already carries.
- **Fold "re-enable and invoke" into one intervention** in `validate_design`, when the
  skills an arm re-enables are exactly the skills it asks for. A treatment that
  re-enables X but asks for Y, or re-enables two skills and asks for one, still
  warns.
- **Grow `minimal` from the bottom.** Measure `minimal` against `full` first, as a
  group ablation of everything outside `minimal`. Then trial one skill at a time on
  cases it targets plus one general case. A skill joins `minimal` only on a win on its
  targets with no loss on the general case. Replacing a skill goes through
  `make refine-skill`. Every project carries `minimal`, so joining it is a stronger
  claim than an opt-in `include`.
- **Choose cases with room.** A case the model always passes returns `no_signal`
  (ADR-0086).

## Consequences

- A skill added to the catalog has a cheap, named path to a verdict.
- Trials cost money. Each is two arms × cases × repetitions, and runs one at a time,
  because a skill ablation regenerates the shared `.claude/skills` ([ADR-0011](0011-per-run-skill-ablation-and-staging.md)).
- With Enforce, the trial measures "does it help when used", not "does the agent find
  it". A skill that wins here may still never fire unasked. Check the `skill_invoked`
  rate of an unenforced follow-up before relying on it in `minimal`.
- Agent bindings (`skills:` in `.github/agents/*.md`) are not filtered by profile. A
  `minimal` project whose tester binds `sensible_testing` still reads it through the
  agent. This is out of scope here; it is a known gap.
