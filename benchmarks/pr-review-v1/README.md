# pr-review-v1 — review a pull request against a hidden answer key

**Hidden from the agent** (`agent_hidden`): this file, `scoring.md`, `grader/` and
`candidate-skill/`.

The workspace starts as a small stdlib shop backend with a PR already applied
(`starter/`, ADR-0068): `PR.md` describes it, `pr.diff` is the diff, and the PR's own
tests pass. The agent writes `review.json`; it must not touch the code.

## What it measures

Whether a harness makes an agent a better **reviewer**: recall over seeded defects of
distinct classes, precision against decoys and shotgun reviews, the right merge verdict,
and leaving the code under review alone. It is the case the candidate skill
`review_pull_request` (`candidate-skill/`) is trialled on.

## Grading

`grader/grade.py check <name> <workspace> <run_id>`: one command per check, names opaque
(`k01`…) because `task.yaml` is readable by the agent. See `scoring.md` for levels and
the calibration. Pure stdlib, no network, no `validation_requirements`.

The answer key is a committed file, so `git show` can reach it (ADR-0079 "not closed").
The `canary` check is the guard: the key carries a secret token, and a run whose
transcript or workspace contains it fails the check. Claude Code's stream-json records
every tool result, so the guard settles those runs; for opencode it is inconclusive.

## Trialling the skill

```bash
make stage-candidate SKILL=review_pull_request
```

Then **Evaluate → New evaluation → Add skills** on `pr-review-v1`, tick
`review_pull_request`, **Enforce skill use**, `harness` prompt, 3+ repetitions, a model
that does not saturate the case. See `docs/guide-review-and-security-cases.md`.

## Changing the case

A new defect or decoy is a new instrument (ADR-0073): bump `version`, re-run the
calibration (`data/reference/pr-review-v1/{gold,shotgun,approve}/`) and regenerate
`pr.diff` from a base tree. Never describe the key's locations outside the hidden paths.
