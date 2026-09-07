# ADR-0041: A skill under test is invoked, not just available

- **Status:** Accepted
- **Date:** 2026-09-04
- **Deciders:** Marcos Soto
- **Related:** [ADR-0017](0017-staged-skills.md),
  [ADR-0028](0028-evaluation-modes.md),
  [ADR-0034](0034-score-registry.md),
  [ADR-0038](0038-every-run-leaves-with-a-score.md),
  [ADR-0040](0040-subject-validation-mode.md)

## Context

The first real Lane B comparison in this lab (`does-adding-caveman-improve-outcomes`:
one AI-drafted snake case, `claude/haiku`, five repetitions per arm) came back
"No difference observed". Every secondary delta sat within run-to-run variance. The
numbers were correct and the caveats honest. The treatment had not happened.

Parsing the step events showed that in **zero of five** treatment runs the agent called
the `Skill` tool or read `caveman/SKILL.md`. The mechanism explains it: `--extra-skill`
projects a staged skill into the agent's native skill view, which makes it *available*.
Whether it is *invoked* is the agent's decision, taken from the case instruction and
the skill's own `description`. `caveman` triggers on "/caveman", "caveman mode", "be
brief"; the instruction said none of that. The lab measured the skill's presence on a
menu and reported it as the skill's effect.

This is not specific to that skill. Skills come in two kinds. **Task skills** ("use when
generating e2e tests") are opened when the task matches, and "available" is a real
treatment for them — provided the case is that task. **Mode skills** (terse output,
strict TDD, a house style) trigger on a phrase the user says, and available is
nothing at all. The most common thing an engineer brings to this lab is one specific
skill they want to evaluate, and it is very often the second kind.

There are therefore two different questions, and the lab could only ask one:

- *Does the skill help when the agent is asked for it?* — the question for one
  specific skill, and the one most people arrive with.
- *Does the agent reach for the skill unasked?* — the question for a whole harness of
  skills, where forcing every one of them would be an unnecessary invocation and the
  interesting observation is which ones the agent chose, and why.

Neither question is answerable without knowing whether invocation happened. Nothing in
the pipeline recorded it: the audit judges from logs, `governance_read_coverage` counts
documents, and the trajectory kept `Skill` calls only as one step among many.

## Decision

**Asking is part of the arm.** `RunConfig` gains `skills_invoke: list[str]`: the skills
the prompt tells the agent, at the start of every step, to invoke and follow. The
runner receives it as `--invoke-skill NAME` and prepends a short activation block to
the first prompt and to every continuation prompt — each step is a fresh agent session
with no memory of having been asked. The block names the mechanism ("invoke", "read its
SKILL.md") so the request leaves a trace, not just a style change.

Every name must be **available** to the run: staged via `skills_extra`, or governed and
not in `skills_disabled`. Preflight refuses anything else, and the runner refuses it
again against the effective skill set. Asking for a skill the agent cannot see would
record "asked and not invoked" about a skill that was never there.

`skills_invoke` enters the condition identity under the same non-empty-only rule as the
other ablation fields, is a locked field of a launched experiment, counts as a harness
axis for `harness_evaluation` (the same skill, on the menu versus called for, is a
legitimate comparison), and is carried through resume, clone, continuation and batch
expansion exactly as `skills_extra` is.

**Invocation is observed and stored.** The trajectory records every `Skill` call by
name and every read of a `skills/<name>/SKILL.md` path, from either event source. A new
evaluator emits two **observations** (never judgements) for a run that is *about* some
skill: `skill_invoked`, the share of the skills under test the agent actually opened,
and `skill_invocations`, how many times. "Under test" is what the run staged or was
asked for, plus what a sibling arm of its experiment removes, minus what the run itself
cannot see — so the baseline of an ablation is scored on the skill being taken away. A
run about no skill emits nothing; a run with no recorded trajectory emits nothing,
because "not observed" must not be stored as "not used".

**The verdict says it first.** Before any delta, the experiment summary counts, per
arm, how many runs opened at least one of their skills. An arm at zero gets a caveat at
the top: it measured availability, not use, and the reader is told how to fix the design
(enforce, or change the skill's triggers). An arm that mixes runs with and without
invocation is flagged as mixing treatments. Subject validation carries the same caveat,
because a tool whose driver skill was never opened was not validated.

**The UI exposes the choice, not a default answer.** New evaluation gets *Enforce skill
use* for the skill factors: on, the arm that has the skills is also asked to use them;
off, the skills are only available. It applies to the whole selection, so a
multi-selection is either all asked for or all left to the agent — the two questions
above, never a mix. Validate your tool defaults to asking, because that mode exists to
check that one specific skill drives one specific tool. The locked configuration of an
experiment now reports its skill lists, so a relaunch carries them.

## Consequences

The comparison a person most often wants — one specific skill, asked for, against the
harness without it — becomes a checkbox, and its result carries a column that says
whether the treatment happened. The comparison that was already possible — a set of
skills on the menu, the agent choosing — keeps its meaning and gains the same column, so
"which skills did it reach for" is now a number per run rather than a grep.

Two experiments that differ only in `skills_invoke` are different arms. Historical
arms are untouched: the field enters the hash only when non-empty.

Costs accepted. The activation block is prompt text, and prompt text is a treatment of
its own: an experiment with enforcement measures "the skill plus being told to use it",
and the arm description says so. The observation is a lower bound: a skill whose body
was injected by a harness without a `Skill` call or a file read leaves no trace and
scores zero; the score's description says so, and the reason string names what was
looked for. The existing `does-adding-caveman-improve-outcomes` runs can be re-scored to
gain the new column, but their verdict will now open with the caveat that the treatment
never happened — which is the correct reading of them.

## Alternatives considered

- **Put the activation phrase in the case instruction.** Rejected: the case must be
  identical in both arms, and a phrase asking for a skill the baseline does not have
  turns the baseline into "asked for something absent". The ask belongs to the arm.
- **Make availability trigger invocation by rewriting the skill's `description`.**
  Rejected as the only mechanism: it changes the skill under test, and every edit is a
  new `condition_hash`. It remains the right move for the *unasked* question, and the
  guide says so.
- **Treat `skill_invoked` as a judgement from the audit.** Rejected: the audit reads
  logs and opines; a `Skill` call in the event stream is a fact of the record. Mixing
  the two is what [ADR-0034](0034-score-registry.md) forbids.
- **Refuse to compute a verdict when the treatment arm never invoked its skill.**
  Rejected: the numbers are true, and blocking hides them. Leading with the caveat is
  the same choice ADR-0040 made for a design that cannot support the conclusion.
