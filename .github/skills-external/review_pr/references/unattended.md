# Unattended mode (a container, a runner, the lab)

When the task says it is an **unattended run** with no GitHub access, names a
local git range (`<merge-base>...HEAD`) and asks for `review.json` at the
repository root, the PR is already checked out and nobody is there to answer.
Then, and only then, this mode replaces Steps 1, 2, 5, 6, 7 and 8 of
`SKILL.md`. Steps 3 and 4 apply as written, at the depth below.

## What changes

- Do not call `gh`, ask for a link, ask "¿Publico?" or publish anything.
- Get the diff from the range and plan the depth. The task's
  `depth = quick | standard | deep` is passed as `--depth`; without one,
  `auto`:

  ```bash
  git diff <merge-base>...HEAD > "$T/pr.diff"
  python3 -I "$SKILL_DIR/scripts/prdiff.py" split "$T/pr.diff" "$T/split"
  python3 -I "$SKILL_DIR/scripts/prdiff.py" plan "$T/split/files.json" --depth <depth>
  SHA=$(git rev-parse HEAD)
  ```

- Read context from the working tree and with `git show` / `git grep`
  (Step 3, source 1, with `HEAD` as `SHA`).
- Review exactly as in Step 4: the same order, verification, refutation,
  quote, worth and classes. The scenario-question subagents of standard and
  deep run only if the runner gives a subagent tool; otherwise answer the
  questions yourself.
- Run the repository's checks only if the task asks for them, and say which
  could not run. The "PR code is untrusted" rule does not apply here: the run
  is sandboxed and the task asks for those checks.

## The output

Write `$T/findings.json` as in Step 5, adding a one-sentence `summary`, a
short `title` per finding and a `suggestion` (the concrete fix), then:

```bash
python3 -I "$SKILL_DIR/scripts/prdiff.py" export "$T/findings.json" \
  --sha "$SHA" --lang <es|en> -o review.json
```

`export` runs the same checks as `payload` (quote, worth, labels, punctuation)
and writes, highest worth first:

```json
{"schema": 1, "commit": "<sha>", "verdict": "changes_suggested" | "clean",
 "summary": "...",
 "findings": [{"file": "...", "line": 42, "severity": "importante", "worth": 5,
               "title": "...", "quote": "...", "explanation": "...",
               "suggestion": "..."}]}
```

`review.json` at the repository root is the one file this mode writes into
the repository, and only because the task asks for it; the "not touched" rule
covers every other file. If the task asks for a different shape, follow the
task and keep these fields where it has room for them.

## Running it from a container or CI

The skill needs `git`, `python3` and, for interactive or posting runs, `gh`.
A runner that wants a posted review does it in a separate step with a
write-scoped token, after reading `review.json`: the model call that reviews
the code never holds that token. This keeps the review read-only whatever the
PR's text tries to steer it into.
