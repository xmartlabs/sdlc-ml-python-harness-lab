---
name: review_pr
description: Use when the user passes a GitHub pull request link to review, e.g. "/review_pr https://github.com/org/repo/pull/123", "revisá este PR <link>", "review this PR". Reads the PR with the user's gh account, looks for real bugs, logic errors, edge cases, security issues and missing tests, shows a short summary in chat, asks once "¿Publico?", and only on yes posts one review (APPROVE or COMMENT) with inline comments in Spanish.
argument-hint: "<link-del-PR>"
---

# Skill: review_pr

Review one GitHub pull request with the user's own `gh` account, show the
findings in chat, and publish a single review only after an explicit yes.

The skill is portable: it works from any repository that carries it. The
judgement (what is a bug, how to word it) is in this file; the mechanics
(splitting the diff, the lines GitHub accepts, the review's event) are in
`scripts/prdiff.py`, Python 3 standard library, no network.

## Unattended mode

When the task says it is an **unattended run** with no GitHub access, names a
local git range (`<merge-base>...HEAD`) and asks for `review.json` at the
repository root, the PR is already checked out and nobody is there to answer.
Then, and only then, this mode replaces Steps 1, 2, 5, 6, 7 and 8:

- Do not call `gh`, ask for a link, ask "¿Publico?" or publish anything.
- Get the diff from the range: `git diff <merge-base>...HEAD > "$T/pr.diff"`,
  then `split` it as in Step 2. Read context from the working tree and with
  `git show` / `git grep` (Step 3, source 1, with `HEAD` as `SHA`).
- Review exactly as in Step 4: the same order, verification and classes.
- Write the result to `review.json` at the repository root, in the shape the
  task asks for. It is the one file this mode writes into the repository, and
  only because the task asks for it; the "not touched" rule below covers
  every other file. Keep the comments in Spanish and worded as Step 7 says.
- Run the repository's checks only if the task asks for them, and say which
  could not run. The "PR code is untrusted" rule does not apply here: the run
  is sandboxed and the task asks for those checks.

Everything else in this file assumes the interactive flow.

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
  line when the PR is fetched (size, where the context is read from) and one
  when the review starts, so the user is never left without news.

## Setup

- `SKILL_DIR`: the folder this file is in. Call the script as
  `python3 "$SKILL_DIR/scripts/prdiff.py"`.
- `T`: a new temporary directory for this run: the session scratchpad if there
  is one, otherwise `mktemp -d`. Never inside a repository.

## Step 1: Parse the link

Take the link from the skill argument. Accept
`https://github.com/<owner>/<repo>/pull/<n>` with any suffix (`/files`,
`/commits`, `#...`, `?...`):

```
^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)
```

Set `O`, `R`, `N` and `URL=https://github.com/$O/$R/pull/$N`.

If there is no argument or it does not match, ask and wait:

> Pasame el link del PR (por ejemplo, https://github.com/org/repo/pull/123).

## Step 2: Fetch the PR

```bash
ME=$(gh api user --jq .login)
START_ACCOUNT=$ME
gh pr view "$URL" --json number,title,body,author,state,isDraft,baseRefName,headRefName,headRefOid,additions,deletions,changedFiles,files,url > "$T/meta.json"
gh pr diff "$URL" > "$T/pr.diff"
python3 "$SKILL_DIR/scripts/prdiff.py" split "$T/pr.diff" "$T/split"
```

Keep `SHA` = `headRefOid` and `AUTHOR` = `author.login`; every line number and
the review itself anchor to `SHA`. `split` writes one diff per file under
`$T/split/files/`, `files.json` (counts, and which files are generated, binary
or deleted) and `commentable.json` (the new-side lines GitHub accepts a
comment on). Read the files one by one, not the whole diff at once.

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

Look for, in this order:

1. **Bugs and logic errors:** wrong conditions, off-by-one, inverted logic,
   wrong variable, unhandled `None`/`null`, broken error handling, race
   conditions, resource leaks, changes that break existing callers.
2. **Edge cases:** empty inputs, zero, limits, encodings, time zones,
   concurrency, retries, partial failures.
3. **Security:** injection (SQL, shell, template), missing auth or permission
   checks, secrets in code or logs, unsafe deserialization, path traversal,
   sensitive data exposed.
4. **Missing tests where they matter:** new or changed logic with real risk
   and no test covering it. Not "every function needs a test".

Do not comment on style, naming, formatting, import order, or personal
preference, unless it clearly causes a problem.

**Verify before reporting.** Each finding must be confirmed against the code at
`SHA`, not inferred from the diff alone: trace a concrete input from where it
enters to the faulty line and state what happens. A split diff shows only the
hunks, so a step that "disappeared" may sit outside them; read the whole file
at `SHA`, and the base, before calling it removed. Drop what you could not
confirm. A serious concern you could not settle may stay as `menor`, worded as
a question.

**Classify every finding:**

| Class | When |
|---|---|
| `importante` | Breaks behaviour, crashes, loses or corrupts data, opens a security hole, breaks a contract or migration, or leaves the PR's core risky logic untested. |
| `menor` | Real but low impact: a rare edge case that fails gracefully, a test worth adding, a clearer error path. |

**Anchor every finding** on the line of the new file where the defect is. Check
it against `$T/split/commentable.json`. A finding outside those lines still
counts: `payload` moves it to the review body as `path:line`.

## Step 5: Summary in chat, then one question

Write the findings to `$T/findings.json`:

```json
{
  "body": "<cuerpo de la review, ver Step 7>",
  "findings": [
    {"path": "src/app.py", "line": 42, "start_line": null,
     "severity": "importante", "comment": "**Importante:** ..."}
  ]
}
```

Each `comment` is **exactly** what will be posted (Step 7 says how it reads),
so the user sees what will go out. Build the review now, as a dry run:

```bash
python3 "$SKILL_DIR/scripts/prdiff.py" payload "$T/findings.json" "$T/split/commentable.json" \
  --sha "$SHA" --author "$AUTHOR" --me "$ME" -o "$T/review.json"
```

It prints the event and why, and how many comments go inline and how many to
the body. It refuses a malformed finding or an em dash / double hyphen used as
punctuation: fix the text and run it again. Then write in Spanish, short:

```
**PR:** <título> (#<n>, de @<autor>)
**Qué hace:** <1 a 3 frases>

**Hallazgos**
1. [importante] `path/archivo.py:42`: <comentario tal cual se publicaría>
2. [menor] `path/otro.ts:17`: <comentario>
(o "Sin hallazgos.")

**Plan:** <Aprobar | Aprobar con N comentarios menores | Solo comentar: hay N hallazgos importantes | Solo comentar: el PR es tuyo y GitHub no deja aprobarlo>

¿Publico?
```

The **Plan** line follows the event `payload` printed. Add a line before it
only when it applies: draft, closed or merged, files not reviewed (generated
ones included), findings that go in the body because they fall outside the diff.

Then stop and wait.

## Step 6: Read the answer

- **Yes** ("sí", "dale", "publicá", "ok"): publish.
- **Yes with edits** ("sí, pero sacá el 2", "pasá el 1 a menor"): edit
  `findings.json`, run `payload` again (the event may change), publish without
  asking again, and mention the change in the final message.
- **Anything else** (no, wait, edits without a yes): do not publish. If there
  were edits, show the updated draft and end without asking again. The user
  will say when to publish. Still run the cleanup in Step 8.

## Step 7: Publish

1. **Check the head did not move:**
   `gh pr view "$URL" --json headRefOid --jq .headRefOid`. If it differs from
   `SHA`, do not publish: say new commits arrived, the line numbers may be off,
   and offer to run the review again.
2. **The body** (Spanish, one or two sentences), set in `findings.json`:
   - No findings: "Revisé el cambio y no encontré problemas. Aprobado."
   - Only minor: "Aprobado. Dejo N comentarios menores, ninguno bloquea."
   - Any importante: "Dejo N comentarios. Los marcados como importantes conviene resolverlos antes de mergear."
   - Own PR: the same text, without saying "Aprobado".
3. **Each inline comment** in Spanish, like a teammate: one to three sentences
   that say what fails, in which case, and the concrete fix. Start with
   `**Importante:**` or `**Menor:**`. When the fix is an exact replacement of
   the commented line, add a GitHub suggestion block:

   ````
   ```suggestion
   <línea corregida>
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
