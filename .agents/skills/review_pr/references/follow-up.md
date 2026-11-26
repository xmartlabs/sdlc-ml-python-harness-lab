# Follow-up: re-check a PR after the author answered

For "respondió los comentarios, revisalo" or `--follow-up`. Run Steps 1 to 3
of `SKILL.md` first (link, fetch, context), then this, instead of Steps 4 to 7.

## 1. What this account said, and what changed since

```bash
gh api "repos/$O/$R/pulls/$N/reviews" --paginate \
  --jq '.[] | {id, user: .user.login, state, commit_id, submitted_at, body}'
gh api "repos/$O/$R/pulls/$N/comments" --paginate \
  --jq '.[] | {id, pull_request_review_id, in_reply_to_id, user: .user.login,
              path, line, original_line, original_commit_id, created_at, body}'
gh pr view "$URL" --json headRefOid,reviewDecision,commits
```

- The **last review by `$ME`** sets the baseline: its `id` is `BASE_ID`, its
  `commit_id` is `BASE_REVIEW` and its `state` is `BASE_STATE`. No review by
  `$ME`: say so and run a normal review instead.
- Its findings are of two kinds; take both:
  - the comments with `pull_request_review_id == BASE_ID` and no
    `in_reply_to_id`. New commits often make them outdated, and GitHub then
    sets `line` to null: anchor them by `original_line` at
    `original_commit_id`, and find where that code went at `SHA`;
  - the list under "Fuera del diff:" / "Outside the diff:" in that review's
    `body`, the findings `payload` could not anchor inline.
- The author's answers are the replies (`in_reply_to_id`).
- The new commits are those after `BASE_REVIEW`. Read their diff, not the
  whole PR again, and first check how the two commits relate:

  ```bash
  gh api "repos/$O/$R/compare/$BASE_REVIEW...$SHA" --jq .status
  gh api "repos/$O/$R/compare/$BASE_REVIEW...$SHA" \
    --jq '.files[] | "### \(.filename) +\(.additions) -\(.deletions)\n\(.patch)"'
  ```

  Only `ahead` means the new head builds on the reviewed one. On `diverged`,
  or a 404 because the old commit is gone, the author rebased or
  force-pushed: the compare would mix in base-branch changes the PR does not
  own. Then review the whole PR diff (Step 2 of `SKILL.md`) at `quick` depth
  instead of section 3, and say so in the summary.

## 2. Decide each finding: FIXED, STILL_OPEN or CANNOT_TELL

You are not reviewing the PR again. For each earlier finding, answer one
closed question: is the defect still reachable at `SHA`?

- Follow the fix wherever it went: a guard moved into a helper, a value
  normalised at the producer, a default changed in config. A fix you did not
  find is not a fix that is not there.
- **FIXED** and **STILL_OPEN** need the exact quote at `SHA` they rest on.
  Without one, the answer is CANNOT_TELL.
- **Declaring a defect fixed is the expensive mistake here**, the opposite of
  a first review: it retires a real bug on your word. When unsure, say
  CANNOT_TELL; it counts as open.
- An author's reply that disagrees ("es intencional", "won't fix") is not a
  fix. Weigh the argument, and say whether you agree; do not mark it FIXED.

## 3. The new commits

Review only what the new commits changed, at `quick` depth with the
checklist. A new defect there is a new finding with a quote and a worth, as
in Step 4 of `SKILL.md`.

## 4. CI and the standing review

```bash
gh api "repos/$O/$R/commits/$SHA/check-runs" --jq '.check_runs[] | "\(.name): \(.status) \(.conclusion)"'
```

Name the checks still running or failed. Then say whether this account's
earlier review still stands: an APPROVE survives a push unless the
repository dismisses stale reviews (`reviewDecision` and the review's `state`
show it).

## 5. Summary and the one question

Write in the user's language:

```
**PR:** <título> (#<n>), seguimiento desde <BASE_REVIEW[:7]>
1. <hallazgo> → FIXED (`<quote>`)
2. <hallazgo> → STILL_OPEN (`<quote>`): <por qué>
**Commits nuevos:** <sin hallazgos | N hallazgos>
**CI:** <verde | corriendo: ... | falló: ...>
**Tu review anterior:** <sigue vigente | fue descartada>

**Plan:** <Nada que publicar | Publicar una review: ...>
```

- Everything FIXED, nothing new, and the earlier review is a standing
  APPROVE: there is nothing to publish. Say so and do not ask "¿Publico?". A
  second approval is noise.
- Everything FIXED, nothing new, and the earlier review was a COMMENT (it
  held importante findings, so it never approved): build an APPROVE with no
  findings as in Step 5 of `SKILL.md` and ask "¿Publico?" once. `payload`
  still refuses to approve the user's own PR; then there is nothing to
  publish.
- Something STILL_OPEN, a new finding, or the earlier review was dismissed:
  build the review as in Step 5 of `SKILL.md` (findings, `payload`, dry run)
  and ask "¿Publico?" once. Steps 6 to 8 apply as written.
