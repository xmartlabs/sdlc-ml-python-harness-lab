# ADR-0092: A skill is trialled on a chosen base, and improved in an audited loop

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Marcos Soto
- **Related:** [ADR-0089](0089-a-profile-grows-one-trialled-skill-at-a-time.md),
  [ADR-0091](0091-a-case-that-ships-a-skill-offers-its-trial.md),
  [ADR-0085](0085-a-project-carries-a-skill-profile-not-the-whole-catalog.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md)

## Context

The question the user brought: "should the PR-review skill go into the template?" It is
really two questions:

- does the skill help on top of `minimal`, which every new project carries?
- does it help on top of `full`?

The lab could only ask the second one. ADR-0091's case panel added a staged skill to the
full harness. Asking both questions at once as one four-arm experiment would vary two
harness factors, and the design validator rightly warns against that.

Improving a skill had the same problem. The LLM audit worked per run and per experiment,
never per skill, and v1 vs v2 existed only as `make refine-skill`.

## Decision

**A skill trial names its base.**

- `POST /api/experiments/skill-trial` accepts `base: minimal | full | both`.
- Each base is one clean A/B: the base against the base plus the skill, asked for. The
  design is `skill_trial.base_design`.
- A **staged** skill is added with `skills_extra`. A **catalog** skill is already in
  `full`, so it is trialled only on `minimal`. The endpoint refuses `full` and `both`
  for it, with the reason.
- `both` creates the two A/Bs in one family (`skill-<skill>-<model>`) so they are read
  side by side.
- With `both` and `reuse_measured` (the default), a base that was **already measured** is
  reused, not re-run. Measured means all of the following:
  - the same family, cases and arms, down to the overrides;
  - the same skill content: the staged skill's `content_hash` is recorded in the
    experiment's notes;
  - at least as many finished runs per arm as requested.

  A draft that never ran, a shorter measurement, or an edited skill is never reused. A
  single base always creates. `dry_run` reports which bases would be reused or created
  and writes nothing; the guided flow uses it to show the real run count before
  creating.
- Without `base`, the endpoint behaves exactly as before.

**A skill is improved in an audited loop.**

- `POST /api/skills/{name}/audit` reads an experiment's evidence for the arms that
  carried the skill, using the same `build_evidence` as the experiment audit. It sets
  that evidence beside the skill's SKILL.md and returns:
  - what the skill covers;
  - what it misses, each item tied to a failed check;
  - a full v2 **draft**.
- The audit writes nothing. Staging the reviewed draft is a separate, explicit call:
  `POST /api/skills/drafts`.
- `POST /api/experiments/skill-refine` sets v1 against v2:
  - a staged v1 is compared add-for-add;
  - a governed v1 is swapped out, as `make refine-skill` does;
  - both use `harness_lab_app.skill_refine`, so the CLI and the UI build the same
    experiment.
- Every one of these experiments is **created, never launched**. Each arm pins its model
  and harness.

**Two read-only helpers serve the guided flows** (ADR-0093):

- `GET /api/estimate`: the median cost and minutes per run for a case and model. It
  counts completed runs only; `n=0` means unknown, never free.
- `GET /api/metrics/criteria`: the decision thresholds, read from `statistics.py` and
  `pruning.py`.

## Consequences

- "Minimal or full?" is one choice in the Skills flow. The earlier
  `trial-review-pull-request` experiments sit in family `candidate-skill-trials` and
  record no `skills_disabled`, so they are not reused. They stay readable on their own,
  and the first `both` request runs both bases. Once a base is measured through the
  flow, later `both` requests reuse it.
- No metric, score, hash or launch path changes. Every new endpoint creates an
  experiment the CLI or New evaluation could already create, or reads what exists.
- The skill audit calls the configured LLM transport (gateway first, then Claude CLI),
  so it costs one auditor call. A missing transport is a 503 with the reason, not a
  silent fallback.
- An audited v2 earns nothing until it wins v1 vs v2. Promotion stays manual
  (Skills → Promote).
