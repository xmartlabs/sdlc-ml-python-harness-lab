# Deep review (on top of standard)

Deep runs when `plan` saw a risky path or a very large change, or when asked
with `--deep`. It costs more because a defect there costs more.

## 1. Read the other side of every contract

When the PR calls, serves or is called by something outside the repository
(another service's API, a schema, a shared workflow, a queue message), read
that other side, at the version this PR will meet:

- The other repository's `main`, and any open PR the description links to
  ("the fields come from org/backend#643"). Read its schema files, route
  handler and enums: `gh api "repos/<o>/<r>/contents/<path>?ref=<ref>" -H
  "Accept: application/vnd.github.raw"`.
- Compare field names, types, required vs optional, enum values, status
  codes and error codes, and limits (max items, sizes).
- Ask what happens against the version without the change: an older backend
  that returns a plain 404, a field that is missing, an enum value this side
  does not know.

A value this side handles that the other side never emits is dead code, worth
a `menor`. A value the other side emits that this side does not handle is a
defect.

## 2. Workflows, deploy and secrets

For a change under `.github/workflows/`, deploy or infrastructure:

- Every trigger: can `workflow_dispatch` or `workflow_call` run it from any
  branch? Does a manual input reach a shell, a tag, or a released artifact?
- Every secret: is it a repository secret any workflow on any branch can
  read, or bound to an environment with protected branches?
- Production and its twin: when the PR copies a dev or UAT workflow for
  production, diff the two and check every difference is intended.
- Concurrency groups, and what a re-run or a cancelled run leaves behind.

## 3. A refutation pass on each importante

Before the summary, take each `importante` and argue the opposite: find the
guard, validator, caller or config that makes it unreachable. Where a subagent
tool exists, give each `importante` to one subagent with the main model (not
the small one), whose only job is to refute it with a quote, or say it could
not. A refutation without a quote does not count. A finding that survives
keeps its class; one refuted on *not triggered* or *incorrect premise*
is dropped; one that only half-survives drops to `menor`, worded as a
question.

## 4. Coverage

Say in the summary what deep still did not reach: a system you could not
read, a migration you could not run, a contract whose other side is private.
