# ADR-0059: A design that cannot launch is not created

- **Status:** Accepted
- **Date:** 2026-09-10
- **Deciders:** Marcos Soto
- **Related:** [ADR-0023](0023-batch-run-execution.md),
  [ADR-0033](0033-experiment-above-run.md),
  [ADR-0044](0044-a-single-prompt-case-can-carry-the-full-harness-pair.md),
  [ADR-0047](0047-a-case-exposes-two-prompt-variants-the-task-and-the-task-plus-the-harness.md)

## Context

An experiment selecting one hand-written lab case (`cli-textstats-v1`, which declares
`default` and `harness`) together with three imported SWE-bench instances (which declare
only `swe_default`) was created and then refused at launch, with six lines naming every
case/arm pair that asked for a prompt its case does not define.

Two separate defects produced that.

**The wizard offered a variant most of its cases cannot run.** The prompt pickers were
built from the *union* of the selected cases' variants. The union is right for a batch:
`_resolve_variants` drops the pairs a case cannot run and carries on, so a case
contributing no runs for one variant is documented behaviour. An experiment is not a
batch — it runs one declared design across every case it selected — and
`ExperimentService.launch` refuses the whole launch when any case lacks the variant an
arm asks for. So the form proposed `default`/`harness` (contributed by a single case),
paired the arms with them, and reported no objection.

**The refusal arrived after the experiment was persisted.** Creation and launch are two
calls. The refusal left behind an experiment that can never run, and — because the id is
derived from the question — the next attempt at the same question found the id taken and
was filed under a suffixed one. In a lab where the same question is asked many times,
every refused launch cost a row and a slug. The visible symptom was an id problem; the
cause was a variant problem refused too late.

## Decision

**Refuse at creation what creation can already see.** `create_experiment` now checks the
prompt variant each condition fixes (and the experiment-level `prompt_variants` axis)
against the variants each selected case declares, and raises before anything is
persisted. `launch` keeps the same check for what creation cannot know — an arm that
fixes no `prompt_variant` inherits the one the launch payload carries, and that payload
does not exist yet — and both call sites share one wording, naming the case and the arm.

**The pickers offer the intersection, not the union.** The wizard's prompt selects —
the shared one and the bare/governed pair — list only the variants every selected case
declares. When the selection spans two prompt families the intersection is empty,
creation is blocked, and the message groups the cases by the family they belong to so
the fix is visible. When the intersection is merely narrower than the union, an
advisory says which variants were dropped and why.

## Consequences

An experiment row now implies a design that can launch. The failure mode this closes is
not a wasted launch — nothing ran — but a lab whose experiment list accumulated designs
that never could, each having consumed the id its question derives.

Creation becomes stricter for API clients: a payload that was accepted and then refused
at launch is now refused at creation, with the same message. The suite path
(`experiment_from_suite`) routes through `create_experiment`, so a suite whose cases do
not declare the requested variant is refused there too — earlier than before, and for
the same reason.

The intersection can be empty for a selection that is otherwise reasonable. That is the
honest answer: cases from two prompt families cannot be measured under one design, and
the remedy is two experiments, not one averaged over an empty prompt.
