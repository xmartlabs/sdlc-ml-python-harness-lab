# Create a case

A **case** is one task an agent is given, plus the checks that say whether it did
it. Everything the lab measures is a case run under some configuration, so a good
case is the whole game: if the check cannot fail, or fails for reasons that have
nothing to do with the agent, every number built on it is noise.

There are four ways in. Pick by where the task comes from.

| The task comes from… | Way in | Time |
|---|---|---|
| An idea you have ("a CSV deduplicator with tests") | **Draft it with AI** | 2 min |
| A real GitHub issue with a known fix | **Import from SWE-bench** | 1 min |
| Your own repository's history or an audit | **Draft from a repository** | 5 min |
| Something you want to keep in the lab for good | **Write a `task.yaml`** | 30 min |

## 1. Draft it with AI

**Evidence → Cases → New case.** Type what you want in plain language, pick a
model (or leave the default), and press **Generate the case**. The draft fills the
whole form — instruction, validation commands, rubric, step budget — and stays
editable. Press **Create case** to save it.

What makes a draft worth saving is its **validation command**: a shell command that
exits non-zero when the agent failed. The lab screens every command the model
proposes and drops those that could not measure anything — a command that cannot
run, a check that passes whatever the agent did, a path outside the workspace, a
document handed to an interpreter. Each drop is shown with its reason. A case that
has commands but no rubric is refused, because nothing could grade it. The full
list of refusals and why they exist: [case formation](guide-case-formation.md).

Two other screens draft the same way, with more context:

- **Evaluate → Validate your tool, step 2** drafts a case that exercises *your*
  tool or skill ([bring your repo or skill](journey-bring-your-repo-or-skill.md)).
- **Evaluate → Audit a repository, step 3** drafts a case from *that repository's*
  checkout, so the commands use its real test runner.

**Do this before trusting it:** run the case once with a strong model. If it does
not pass, the case is wrong more often than the model is.

## 2. Import from SWE-bench

SWE-bench instances are real issues from real repositories, each with the tests
that its fix made pass (`FAIL_TO_PASS`) and the ones it must not break
(`PASS_TO_PASS`). They score themselves.

- **SWE-bench Lite / Verified (Python):** **Evidence → Cases → Import SWE-bench
  instance**, type an id such as `pallets__flask-4992`, **Import instance**.
  Or `make swebench-import INSTANCE=pallets__flask-4992`.
- **SWE-bench Pro (multi-language):** **Browse instances**, filter by repository,
  **Import** the row. Or `make swebench-pro-import INSTANCE=<id>`.

Prove an instance grades correctly before spending tokens on it:
`make swebench-selftest INSTANCE=<id>` applies the known fix and checks it
passes. A curated starting set is in [swebench-curated-set.md](swebench-curated-set.md).

## 3. Draft from a repository

When the task should be *your* project's — "fix the flaky retry in the billing
client" — the case has to run inside your repository, against its own tests.
**Evaluate → Audit a repository** registers and pins the repository, audits its
harness, and on step 3 offers cases drawn from what the audit found, from the
repository's own commit history, or written by hand. See
[bring your repo or skill](journey-bring-your-repo-or-skill.md).

## 4. Write a `task.yaml`

For a case you want to keep, version and calibrate, write it under
`benchmarks/<case-id>/`:

```
benchmarks/my-case/
  task.yaml            # id, tags, validation_commands, validation_requirements, prompts
  prompt_default.md    # the task
  prompt_harness.md    # generated: the task + the harness preamble (make prompts)
  tests/test_*.py      # the grade — the lab's tests, never the agent's
  scoring.md           # what 0, 5 and 10 mean
  starter/             # optional: files the workspace starts from (a bug to fix)
```

The lab seeds every `benchmarks/*/task.yaml` on startup and refreshes its prompts
and commands; **Seed benchmark cases** on the Cases page does it on demand, and
`make case-import TASK=benchmarks/my-case/task.yaml` from the shell.

Four rules keep a hand-written case honest
([ADR-0071](adr/0071-a-grader-brings-its-own-dependencies-and-never-the-agents-tests.md)):

1. **Ship the tests; never grade the agent's own.** A case that runs the file the
   agent wrote lets six easy tests outscore twenty-five hard ones.
2. **One validation command per test**, after a tamper guard, so the score has the
   suite's resolution. Copy `benchmarks/clinic-slots-t1/task.yaml`.
3. **Declare what the tests import** under `validation_requirements`, pinned —
   validation runs offline in a clean container.
4. **Calibrate:** an empty workspace must fail every test, a reference solution
   (kept *outside* the repository) must pass every test, and a half-done one must
   land in between.

Worked examples: `clinic-*` (pure Python, a three-tier difficulty ladder),
`eda-iris-pipeline-v2` (pandas and scikit-learn), `frontend-bugfix-v1` (a broken
page graded in a real browser), `todo-web-v1` (a page built from scratch).

## Difficulty tiers

Tag a case `tier:1`, `tier:2` or `tier:3` and any experiment over cases from two or
more tiers shows each model's **capability frontier** — the highest tier it does
reliably. That is how you learn whether a small model is good enough for a kind of
work ([small models](guide-small-models.md)).

**Next:** [run it](journey-run-and-compare.md).
