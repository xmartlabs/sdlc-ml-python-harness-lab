# ADR-0091: A case that ships a skill offers its trial from the UI

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Marcos Soto
- **Related:** [ADR-0087](0087-a-case-can-ship-the-candidate-skill-it-trials.md),
  [ADR-0089](0089-a-profile-grows-one-trialled-skill-at-a-time.md),
  [ADR-0090](0090-the-navigation-is-grouped-by-capability.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md)

## Context

A user ran `pr-review-v1` with two models in parallel: a local Bonsai 27B and
Haiku, through Compare models. The results said nothing about the case's own
skill, `review_pull_request`.

- The runs carried the full catalog. The candidate lives in the case's hidden
  `candidate-skill/` and was never staged.
- Neither run opened a skill: `skills_consulted` was empty for both.
- Haiku reviewed on its own and reached level 4 for $0.29. Bonsai wrote no
  `review.json` and stayed at level 0.

A model comparison answers "which model". It cannot answer "does this skill help".
That needs an A/B with the skill asked for (ADR-0041).

The pieces existed, but the path was spread across four places:

- staging was terminal-only (`make stage-candidate`);
- the A/B was built by hand in New evaluation → Add skills, with Enforce on;
- improving meant editing the staged copy in Skills;
- bringing your own skill meant the same A/B with an imported skill.

Nothing told a newcomer that the case ships a skill to test.

## Decision

**A case that ships `candidate-skill/<name>/` offers three paths in one panel**
under the case editor:

1. try the case's skill;
2. improve it;
3. bring yours.

Every path creates the same experiment the first candidate trials used
(family `candidate-skill-trials`):

- control adds nothing; treatment adds the staged skill and asks for it;
- both arms use the `harness` prompt, with one model and harness pinned.

- `GET /api/cases/{id}/candidate-skills` lists what a case ships and whether
  each skill is staged.
- `POST /api/cases/{id}/candidate-skills/{name}/stage` stages a skill as
  `make stage-candidate` does. Both go through `harness_lab_app.candidate_skills`,
  so they stage the same tree. `replace` is opt-in, as `REPLACE=1` is, so local
  edits are never overwritten silently.
- `POST /api/experiments/skill-trial` gains `source: "staged"`, which builds the
  arms above. `source: "catalog"` (the default) is unchanged: the profile against
  the profile plus one governed skill (ADR-0089).
- The experiment is **created, not launched**. Launching stays on the Experiments
  page, behind the shared form and preflight (ADR-0090).
- `/evidence/cases?case=<id>` opens a case and scrolls to the panel, so hubs and
  guides can link straight to it.

## Consequences

- The PR-review flow the user asked for is a few clicks:
  1. stage `review_pull_request`;
  2. pick a model;
  3. create the A/B;
  4. launch it from Experiments.
- The same panel works for every case that ships a candidate:
  `security-audit-v1`, `security-fix-v1` and `agent-containment-v1` today. A
  new case that ships one gets the panel with no UI change.
- Cases without a candidate still show the "bring yours" path.
- Results land next to the earlier trials in `candidate-skill-trials`, so a
  rewrite of `review_pull_request` is read beside the first verdict
  (7 runs per arm, `no_signal`).
- The summary already reports `skill_invoked` per arm. Read it first.
- No metric, score, run or hash changes. The new endpoints stage a skill or
  create an experiment that New evaluation and the CLI could already create.
