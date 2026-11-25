# ADR-0100: An ad hoc task carries its skill to try, and can review a pull request

- **Status:** Accepted
- **Date:** 2026-11-24
- **Deciders:** Marcos Soto
- **Amends:** [ADR-0095](0095-an-adhoc-task-runs-on-a-copy-and-is-applied-on-request.md)
- **Related:** [ADR-0011](0011-per-run-skill-ablation-and-staging.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  guide [guide-adhoc-tasks.md](../guide-adhoc-tasks.md)

## Context

The loop operators want from an ad hoc task is *"this repository, this skill, run it,
read the numbers, refine the skill"*. Three things got in the way when it was tried on
a real pull-request review:

1. **The skill picker was invisible at the start.** *Skill to try* lived only in the
   task page's *Run with…* panel, after the task existed. The *New task* form, where
   an operator first looks for it, had no such field.
2. **Versions of one skill blurred together.** Editing a staged skill changes its
   content hash and therefore the run's `condition_hash`, but the hash lived only in
   the run's `metadata.json`, which an ad hoc run loses after the retention window. The
   comparison table showed `review_pr` for v1 and v2 alike.
3. **A review task needed hand assembly, and broke silently.** The operator had to
   clone the repository, check the PR out, and write a prompt naming the range. A prompt
   saying `origin/main...HEAD` looked right but could not work: the run's workspace is a
   plain clone of the lab's copy and carries no remote-tracking branches.

## Decision

- **A task stores a default skill to try** (`trial_skill`, `invoke_trial_skill`),
  chosen in the *New task* form and validated against the staged skills. The run panel
  preselects it once when the task loads; every run may still change it. It is a
  default, never a constraint.
- **Each ad hoc run records the content hash of every skill it tried**
  (`AdhocRun.trial_skill_hashes`). It is read when the run is launched, then replaced
  by the hash the runner wrote to the run's `metadata.json` (`skills_extra_hashes`,
  from the tree it actually materialized) once the run ends, so an edit made while
  the run was queued never mislabels it. The table shows `name@<7 chars>`, so two
  versions of one skill stay distinct after the run's files are purged.
- **A task can review a pull request** (`POST /api/adhoc/pr-reviews`, *Review a PR* in
  the form). It takes the PR number, the branch or commit in the folder that holds the
  PR's head, the base branch, and optionally the repository, title, description and
  depth. The lab:
  - resolves the merge-base of head and base **in the operator's folder, read-only**;
  - pins the task at the PR's head (the folder's checkout is never moved);
  - writes the prompt with the range **by SHA**, `<merge-base>...HEAD`, which exists in
    every clone because it is an ancestor of the head;
  - records `pr_ref` (`owner/repo#N`, read from `origin`, SSH host aliases included)
    and `pr_base_ref` (the merge-base);
  - asks for `review.json` and states that the run has no GitHub access.
- **The lab does not fetch the PR.** The API container holds no credential for the
  operator's remote, and an SSH alias does not resolve inside it. The form tells the
  operator the one command to run first (`git fetch origin pull/N/head:pr-N`).
- A PR task's *folder moved* warning is off: the folder's HEAD is not the task's base.

## Consequences

- An operator can stage a skill, open *New task → Review a PR*, pick the skill, and
  compare runs (no skill, skill v1, skill v2) on one fixed PR with the same model.
- Runs only measure the **unattended half** of a review skill: the findings in
  `review.json`. Posting comments to GitHub stays outside the lab by design. A
  review skill built for the interactive flow (`gh`, a PR link, "publish?") needs
  an unattended mode keyed on the run's prompt, as `review_pr` has, or every run
  with it measures the setup rather than the skill.
- Grading is still the generic LLM judge. Precision and recall against known defects
  need a case built from merged PRs with known follow-up fixes; that is future work.
- Migration: four `adhoctask` columns and one `adhocrun` JSON column, added in place
  with defaults; older rows read as "no default skill, not a PR, no hashes".

## Addendum (2026-11-25): how `review_pr` meets the run

- **Depth.** The lab's `depth` (quick, standard, deep) reaches the skill as
  `prdiff.py plan --depth <depth>`. Run interactively with no depth, `plan` picks
  one: quick for at most 3 reviewable files and 80 changed lines; deep past 1500
  lines or when any path is risky (auth, permissions, secrets, credentials,
  payments, billing, migrations, schemas, CI workflows, deploy, Dockerfile;
  case-insensitive, plurals included); standard otherwise. Generated and binary
  files do not count toward size.
- **`review.json`.** `prdiff.py export` writes it after the same checks as a posted
  review (a quote at the head for every finding, worth, labels, punctuation), and
  refuses a finding without a `title`:
  `{"schema": 1, "commit", "verdict": "changes_suggested" | "clean", "summary",
  "findings": [{"file", "line", "severity", "worth", "title", "quote",
  "explanation", "suggestion"}]}`, highest worth first. `explanation` carries no
  severity label: `severity` is its own field.
- **Follow-up** (`--follow-up`, interactive only) re-checks an earlier review's
  findings as FIXED, STILL_OPEN or CANNOT_TELL. It is outside what the lab
  measures.
