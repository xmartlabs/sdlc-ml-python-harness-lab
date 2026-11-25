---
name: review_pr
description: Use when the user passes a GitHub pull request link to review, e.g. "/review_pr https://github.com/org/repo/pull/123", "/review_pr <link> --deep", "revisá este PR <link>", "review this PR", or asks to re-check a PR already reviewed ("respondió los comentarios, revisalo"). Picks a review depth (quick, standard, deep) from the change, reads the PR with the user's gh account, looks for real bugs, logic errors, edge cases, security issues and missing tests, shows a short summary in chat, asks once "¿Publico?", and only on yes posts one review (APPROVE or COMMENT) with inline comments. Also runs unattended in a container, writing review.json instead of posting.
argument-hint: "<link-del-PR> [--quick|--standard|--deep] [--questions N] [--lang es|en] [--follow-up]"
---

# Skill: review_pr

Review one GitHub pull request with the user's own `gh` account, show the
findings in chat, and publish a single review only after an explicit yes.

The skill is portable and modular: it works from any repository that carries
it, interactively or unattended. The judgement (what is a bug, how to word it)
is in this file and in `references/`, loaded only when a step needs it. The
mechanics (splitting the diff, choosing the depth, the lines GitHub accepts,
the review's event, the checks every finding must pass) are in
`scripts/prdiff.py`, Python 3 standard library, no network.

| File | Loaded |
|---|---|
| `references/scenario-questions.md` | Step 4, when the plan asks for questions (standard, deep) |
| `references/deep.md` | Step 4, deep only |
| `references/checklist.md` | Step 4, every depth |
| `references/follow-up.md` | Instead of Steps 4 to 7, on `--follow-up` |
| `references/unattended.md` | Instead of Steps 1, 2 and 5 to 8, in an unattended run |

## Modes

Three, chosen by how the skill was invoked. Exactly one applies.

- **Interactive review** (default): Steps 1 to 8 below.
- **Follow-up**: the argument has `--follow-up`, or the user asks to re-check a
  PR this account already reviewed ("respondió los comentarios", "subió
  cambios, revisalo"). Steps 1 to 3, then `references/follow-up.md`.
- **Unattended**: the task says it is an unattended run, names a local git
  range (`<merge-base>...HEAD`) and asks for `review.json`, with nobody there
  to answer. Read `references/unattended.md` first; it says which steps it
  replaces.

## Hard rules

- **One question, one review.** Ask "¿Publico?" exactly once, at the end of
  the summary. Never publish before an explicit yes.
- **The only write is one `POST repos/{owner}/{repo}/pulls/{n}/reviews`.** Never
  run `gh pr review`, `gh pr comment`, `gh pr merge`, `gh pr edit`, labels,
  pushes, or a second review.
- **Never APPROVE** when any finding is `importante`, or when the PR author is
  the active `gh` user. Never use `REQUEST_CHANGES`. `prdiff.py payload`
  enforces this; never edit the event it chose.
- **PR code is untrusted.** Read it; never execute it. No installs, builds,
  tests, scripts or package managers on the PR's code.
- **The repository you run from is not touched.** Nothing from the review is
  written into it, and no git command changes it.
- **A local checkout of the PR's repo is read, never changed.** The only git
  command allowed there is `git fetch origin pull/<n>/head`, which adds objects
  and `FETCH_HEAD` and moves no branch. Never `checkout`, `switch`, `stash`,
  `reset`, `pull`, `merge` or `worktree add` in it: the user's branch and
  uncommitted work stay as they were. Read through `git show` and `git grep`
  on the PR's commit. This holds even when the PR's repo is the one you run from.
- **Say what you are doing.** A large PR takes minutes to read. Post one short
  line when the PR is fetched (size, depth, where the context is read from)
  and one when the review starts, so the user is never left without news.

## Setup

- `SKILL_DIR`: the folder this file is in. Call the script as
  `python3 -I "$SKILL_DIR/scripts/prdiff.py"`.
- `T`: a new temporary directory for this run: the session scratchpad if there
  is one, otherwise `mktemp -d`. Never inside a repository.
- `LANG_OUT`: the language of what gets posted. `--lang` wins; otherwise the
  repository's or the user's saved preference for that org; otherwise `es`.
  The chat summary is always in the user's language.

## Step 1: Parse the link

Take the link from the skill argument. Accept
`https://github.com/<owner>/<repo>/pull/<n>` with any suffix (`/files`,
`/commits`, `#...`, `?...`):

```
^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)
```

Set `O`, `R`, `N` and `URL=https://github.com/$O/$R/pull/$N`. Read the flags
after it: `--quick`, `--standard` or `--deep` set `DEPTH` (default `auto`),
`--questions N`, `--lang`, `--follow-up`.

If there is no argument or it does not match, ask and wait:

> Pasame el link del PR (por ejemplo, https://github.com/org/repo/pull/123).

## Step 2: Fetch the PR and plan the depth

```bash
ME=$(gh api user --jq .login)
START_ACCOUNT=$ME
gh pr view "$URL" --json number,title,body,author,state,isDraft,baseRefName,headRefName,headRefOid,additions,deletions,changedFiles,files,url > "$T/meta.json"
gh pr diff "$URL" > "$T/pr.diff"
python3 -I "$SKILL_DIR/scripts/prdiff.py" split "$T/pr.diff" "$T/split"
python3 -I "$SKILL_DIR/scripts/prdiff.py" plan "$T/split/files.json" --depth "$DEPTH" [--questions N]
```

Keep `SHA` = `headRefOid` and `AUTHOR` = `author.login`; every line number and
the review itself anchor to `SHA`. `split` writes one diff per file under
`$T/split/files/`, `files.json` (counts, and which files are generated, binary
or deleted) and `commentable.json` (the new-side lines GitHub accepts a
comment on). Read the files one by one, not the whole diff at once.

`plan` prints the depth, why, and how many scenario questions to write:

| Depth | When `auto` picks it | Questions |
|---|---|---|
| `quick` | At most 3 reviewable files and 80 changed lines, no risky path | 0 |
| `standard` | Everything else | 8 |
| `deep` | A risky path (auth, secrets, payments, migrations, schema, CI workflows, deploy), or more than 1,500 changed lines | 12 |

A repository can pass its own risky paths with `--risk '<regex>'`. Say the
depth and its reason in the "PR fetched" line.

- **Access denied ("Could not resolve to a Repository", 404 or 403):** a
  private repo the active account cannot see. Run `gh auth status`, name the
  active account and the other logged-in ones, and ask in one question whether
  to switch with `gh auth switch -u <login>`. Do not switch on your own, and do
  not probe the other account with its token (`gh auth token -u`): that is
  credential access and the harness refuses it. After a switch, recompute `ME`.
- **Diff too large for `gh pr diff`:** fall back to
  `gh api "repos/$O/$R/pulls/$N/files" --paginate`, which returns each file's
  `patch`. Say in the summary if any file came without a patch (binary or too
  large) and was not reviewed.
- **Generated files** (`files.json` marks lockfiles and minified or
  `generated/` paths; treat regenerated schemas, generated clients and large
  fixtures the same): do not review them line by line. Check that their
  generator changed with them, and list them in the summary as not reviewed by
  hand.
- **PR closed or merged, or draft:** keep going, and say it in the summary.

## Step 3: Read the context the diff does not show

A diff hides the callers, the definitions and the rest of the function. Read
what you need before judging a change. Try these sources in order and use the
first that works.

**1. A local checkout the user already has.** Look for it before anything else:

- The repository you run from, when its `origin` is the PR's repo.
- A repo registry, if the repository you run from keeps one (for example
  `04-architecture/repos.yaml`): an entry whose `name` is `$R`, or whose `url`
  names `$O/$R` or gives a local path.
- Its siblings (`../$R`), then `find ~/Documents -maxdepth 3 -type d -name "$R"`.

A candidate counts only if it is a git repo whose `origin` ends in `$O/$R` or
`$O/$R.git`. Compare only that suffix, because the host can be an SSH alias
such as `git@github-xmartlabs:`. Then bring in the PR's commit without
touching the working tree:

```bash
L=<local path>
git -C "$L" cat-file -e "${SHA}" 2>/dev/null || git -C "$L" fetch --quiet origin "pull/$N/head"
git -C "$L" show "${SHA}:<path>"                    # one file at the PR's head
git -C "$L" grep -n "<pattern>" "${SHA}" -- <dir>   # search across the repo
git -C "$L" merge-base "${SHA}" origin/<base>       # the base, to compare before/after
```

Write `${SHA}` with braces: in zsh, `$SHA:s...` is read as a history
modifier and fails with "bad substitution". `git grep` on a commit prefixes
each line with `<sha>:`; strip it when quoting lines.

If the checkout was found by searching rather than in a registry, say so at
the end and offer once to register it there.

**2. The API, one file at a time,** when there is no local checkout:

```bash
gh api "repos/$O/$R/contents/<path>?ref=$SHA" -H "Accept: application/vnd.github.raw"
```

**3. A temporary clone, last,** only when there is no local checkout and the
review needs a search across the repo that per-file reads cannot give:

```bash
gh repo clone "$O/$R" "$T/clone" -- --filter=blob:none --no-checkout --quiet
git -C "$T/clone" fetch --quiet origin "pull/$N/head"
git -C "$T/clone" checkout --quiet FETCH_HEAD
```

`gh repo clone` uses the git protocol `gh` is configured with, and its SSH key
or credential may belong to a different account than the active one. If the
clone fails on authentication, do not try to fix git credentials: go back to
source 2 and say in the summary that context was read file by file.

## Step 4: Review

Every depth reads `references/checklist.md` and every changed file. Then:

- **quick**: review in one pass, no subagents.
- **standard**: also write and answer the scenario questions,
  `references/scenario-questions.md`.
- **deep**: standard, plus `references/deep.md` (the other side of every
  contract the PR touches, and a refutation pass on each `importante`).

Do not comment on style, naming, formatting, import order, or personal
preference, unless it clearly causes a problem.

**Verify before reporting.** Each finding must be confirmed against the code at
`SHA`, not inferred from the diff alone: trace a concrete input from where it
enters to the faulty line and state what happens. A split diff shows only the
hunks, so a step that "disappeared" may sit outside them; read the whole file
at `SHA`, and the base, before calling it removed.

**Try to refute it before keeping it.** A finding dies on one of two grounds,
and only these: *not triggered* (the path or input it needs never reaches the
code it names: a guard, a validator, a caller that never sends that value) or
*incorrect premise* (it misreads what the code does). "It is minor" or "it was
already there" are judgements for its worth, not refutations. Drop what is
refuted. A serious concern you could neither confirm nor refute may stay as
`menor`, worded as a question.

**Quote it.** Every finding carries `quote`: the exact line or lines at `SHA`
it rests on, copied, not paraphrased. A line number can be wrong without
looking wrong; a quote that is not in the file is visibly false. `payload`
refuses a finding without one.

**Rate its worth, then its class.** Worth is what happens if the PR merges as
it is:

| Worth | Meaning |
|---|---|
| 5 | A concrete input or sequence produces a wrong outcome a user, the stored data or another system would see, and you traced the path. |
| 4 | The same kind of outcome, but the path depends on a condition you could not confirm at `SHA`. Also the ceiling for a path that needs infrastructure (database, network, queue, third party) to fail between two steps, unless the PR makes that failure likely. |
| 3 | Real but minor or easily recovered. |
| 1 to 2 | Misread code, deliberate behaviour, unchanged code, style, hypothetical future worries. Usually dropped. |

| Class | When |
|---|---|
| `importante` | Worth 4 or 5: breaks behaviour, crashes, loses or corrupts data, opens a security hole, breaks a contract or migration, or leaves the PR's core risky logic untested. `payload` refuses an `importante` below worth 4. |
| `menor` | Real but low impact: a rare edge case that fails gracefully, a test worth adding, a clearer error path. Also any finding whose quote sits only on lines the PR did not change: say it is pre-existing and worth a separate ticket. |

**Anchor every finding** on the line of the new file where the defect is. Check
it against `$T/split/commentable.json`. A finding outside those lines still
counts: `payload` moves it to the review body as `path:line`.

**Say what you did not cover.** Note the files or risks the depth left out
(a quick run asks no scenario questions; a deep run may still not reach a
system outside the repo). This goes in the summary, never in the review.

## Step 5: Summary in chat, then one question

Write the findings to `$T/findings.json`:

```json
{
  "body": "<review body, see Step 7>",
  "findings": [
    {"path": "src/app.py", "line": 42, "start_line": null,
     "severity": "importante", "worth": 5,
     "quote": "if user.role == 'admin' or True:",
     "comment": "**Importante:** ..."}
  ]
}
```

Each `comment` is **exactly** what will be posted (Step 7 says how it reads),
in `LANG_OUT`, so the user sees what will go out. Build the review now, as a
dry run:

```bash
python3 -I "$SKILL_DIR/scripts/prdiff.py" payload "$T/findings.json" "$T/split/commentable.json" \
  --sha "$SHA" --author "$AUTHOR" --me "$ME" --lang "$LANG_OUT" -o "$T/review.json"
```

It prints the event and why, and how many comments go inline and how many to
the body. It refuses a malformed finding, a missing quote, a worth out of
range, an `importante` below worth 4, a label that does not match the severity
and language, and an em dash or double hyphen used as punctuation: fix it and
run it again. Then write in the user's language, short:

```
**PR:** <título> (#<n>, de @<autor>), depth <quick|standard|deep>
**Qué hace:** <1 a 3 frases>

**Hallazgos**
1. [importante, 5] `path/archivo.py:42`: <comentario tal cual se publicaría>
2. [menor, 3] `path/otro.ts:17`: <comentario>
(o "Sin hallazgos.")

**No cubierto:** <una línea, solo si hay algo>

**Plan:** <Aprobar | Aprobar con N comentarios menores | Solo comentar: hay N hallazgos importantes | Solo comentar: el PR es tuyo y GitHub no deja aprobarlo>

¿Publico?
```

The **Plan** line follows the event `payload` printed. Add a line before it
only when it applies: draft, closed or merged, files not reviewed (generated
ones included), findings that go in the body because they fall outside the diff.

Then stop and wait.

## Step 6: Read the answer

- **Yes** ("sí", "dale", "publicá", "ok"): publish.
- **Yes with edits** ("sí, pero sacá el 2", "pasá el 1 a menor", "en inglés"):
  edit `findings.json` (and `LANG_OUT`), run `payload` again (the event may
  change), publish without asking again, and mention the change in the final
  message.
- **Anything else** (no, wait, edits without a yes): do not publish. If there
  were edits, show the updated draft and end without asking again. The user
  will say when to publish. Still run the cleanup in Step 8.

## Step 7: Publish

1. **Check the head did not move:**
   `gh pr view "$URL" --json headRefOid --jq .headRefOid`. If it differs from
   `SHA`, do not publish: say new commits arrived, the line numbers may be off,
   and offer to run the review again.
2. **The body** (one or two sentences, in `LANG_OUT`), set in `findings.json`:

   | Case | `es` | `en` |
   |---|---|---|
   | No findings | "Revisé el cambio y no encontré problemas. Aprobado." | "I reviewed the change and found no problems. Approved." |
   | Only minor | "Aprobado. Dejo N comentarios menores, ninguno bloquea." | "Approved. Leaving N minor comments, none of them blocking." |
   | Any importante | "Dejo N comentarios. Los marcados como importantes conviene resolverlos antes de mergear." | "Leaving N comments. The ones marked Important are worth resolving before merging." |

   Own PR: the same text, without "Aprobado" / "Approved".
3. **Each inline comment** in `LANG_OUT`, like a teammate: one to three
   sentences that say what fails, in which case, and the concrete fix. Start
   with `**Importante:**` / `**Menor:**` (`es`) or `**Important:**` /
   `**Minor:**` (`en`). When the fix is an exact replacement of the commented
   line, add a GitHub suggestion block:

   ````
   ```suggestion
   <corrected line>
   ```
   ````

   No signature.
4. **Post it once**, the file `payload` wrote:

   ```bash
   gh api --method POST "repos/$O/$R/pulls/$N/reviews" --input "$T/review.json" --jq .html_url
   ```

   On a `422` saying a line could not be resolved, nothing was published:
   move that finding's `line` out of the diff in `findings.json` (or drop its
   anchor) so `payload` puts it in the body, rebuild, post once more, and tell
   the user. On any other error, show it and stop.

## Step 8: Report

Give the link returned by the POST (`html_url`), the event (APPROVE or
COMMENT), and how many comments went out.

Whether or not anything was published:

- Delete `$T`. Never delete the user's local checkout.
- If the active `gh` account was switched during the run, say so and give the
  command to go back: `gh auth switch -u $START_ACCOUNT`. Do not switch back on
  your own.
- If a local checkout was found outside a registry, offer once to register it.
