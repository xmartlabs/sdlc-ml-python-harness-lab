# ADR-0044: A single-prompt case can carry the full-harness pair

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Marcos Soto
- **Related:** [ADR-0023](0023-batch-run-execution.md),
  [ADR-0028](0028-evaluation-modes.md),
  [ADR-0031](0031-harness-source-is-a-run-condition.md),
  [ADR-0033](0033-experiment-above-run.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  `frontend/src/pages/NewEvaluation.tsx`,
  `src/harness_lab_app/experiment_service.py`

## Context

The *Full harness* factor was written around the lab's own founding case,
`tetris-v1`, which ships a whole prompt family (`naive`, `incremental`,
`conductor`, `sdlc`, `sdlc_full`). The bare arm took the plain member of that
family, the treatment arm the governance-loaded one, and the wizard refused any
selection whose cases defined fewer than two variants.

Every case an engineer writes in the Cases screen defines exactly one variant,
`default`. So the founding comparison was unavailable for the cases the lab is
actually used on — and unavailable precisely where it is most defensible. A
hand-written `sdlc` prompt already *tells* the agent to read governance; part of
the measured difference between `naive` and `sdlc` is the instruction, not the
harness. A plain `default` prompt says nothing about governance, so running it
on both arms isolates the harness cleanly.

The form contradicted itself about this. The branch immediately after the
refusal states, in a comment, that equal prompts are *not* degenerate — the bare
arm still hides the whole governed catalog — and blocks only the case where
there is nothing to hide. The variant-count gate refused designs the next branch
described as sound.

Two further gaps sat behind it:

- **Nothing asks the agent to use the harness.** With a plain prompt on both
  arms, the treatment arm may never open a skill, and the pair then measures
  what sat on disk rather than what the harness did — the failure ADR-0041 was
  written about. The mechanism (`skills_invoke`) existed but was offered only by
  the two skill factors and by subject validation.
- **An undefined variant ran empty.** `ExperimentService.launch` crossed every
  case with every variant and applied each arm's `prompt_variant` override
  without checking the case declares it. The prompt lookup falls back to `""`,
  so the run went out with no task at all — inside one arm, where it reads as
  that arm losing. Batch mode has resolved this per case since ADR-0023;
  experiments did not.

## Decision

**A prompt family is not a precondition of the full-harness pair.** The
variant-count gate is removed. A single-variant case runs the factor with the
same prompt on both arms; the design is refused only when the arms would be
genuinely identical — same prompt with no governed skills to hide.

**What the pair measures is stated, not assumed.** The wizard prints two
advisories for a same-prompt design: the treatment is the *skill catalog*, not
the harness as a unit; and inside *This lab* both arms still carry the
governance body of `CLAUDE.md`, since the ablation rewrites only its generated
skills region. A control side carrying nothing of the harness needs
*Governance source* (`native` vs `injected`) against an external repository.

**The full-harness arm can be told to use the harness.** *Ask the agent to use
the harness* puts the named governed skills into the ADR-0041 activation block
on the treatment arm only — the bare arm cannot see them, and preflight refuses
a run told to invoke a skill it hides.

**A launch refuses a prompt variant its case does not define.** `launch`
resolves the effective variant per (case, arm) against the case's declared
variants and raises before persisting anything, listing every offending pair.

## Consequences

- The comparison the lab exists for is available on cases written in the lab,
  which is what an engineer arriving with one task actually has.
- A same-prompt result carries a narrower claim than the factor's name, and the
  wizard says so on the way in rather than the write-up discovering it later.
- Mixing prompt families inside one experiment now fails loudly at launch
  instead of producing empty runs that look like a treatment effect.
- The bare arm inside the lab is still not bare. That limit is now written down
  in the UI, the guide and here, and the route around it (ADR-0031, external
  repository) is named.
