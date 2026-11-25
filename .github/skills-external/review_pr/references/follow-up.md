# Follow-up: re-check a PR after the author answered

For "respondió los comentarios, revisalo" or `--follow-up`. Run Steps 1 to 3
of `SKILL.md` first (link, fetch, context), then this, instead of Steps 4 to 7.

## 1. What this account said, and what changed since

```bash
gh api "repos/$O/$R/pulls/$N/reviews" --paginate \
  --jq '.[] | {id, user: .user.login, state, commit_id, submitted_at}'
gh api "repos/$O/$R/pulls/$N/comments" --paginate \
  --jq '.[] | {id, in_reply_to_id, user: .user.login, path, line, created_at, body}'
gh pr view "$URL" --json headRefOid,reviewDecision,commits
```

- The **last review by `$ME`** sets the baseline: its `commit_id` is
  `BASE_REVIEW`. No review by `$ME`: say so and run a normal review instead.
- Its findings are the top-level comments by `$ME` in that review; the
  author's answers are the replies (`in_reply_to_id`).
- The new commits are those after `BASE_REVIEW`. Read their diff, not the
  whole PR again:

  ```bash
  gh api "repos/$O/$R/compare/$BASE_REVIEW...$SHA" \
    --jq '.files[] | "### \(.filename) +\(.additions) -\(.deletions)\n\(.patch)"'
  ```

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

- Everything FIXED, nothing new, the earlier review still stands: there is
  nothing to publish. Say so and do not ask "¿Publico?". A second approval
  is noise.
- Something STILL_OPEN, a new finding, or the earlier review was dismissed:
  build the review as in Step 5 of `SKILL.md` (findings, `payload`, dry run)
  and ask "¿Publico?" once. Steps 6 to 8 apply as written.
