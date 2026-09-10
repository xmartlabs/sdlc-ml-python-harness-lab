# ADR-0057: A case is validated before it is allowed to measure anything

- **Status:** Accepted
- **Date:** 2026-09-09
- **Deciders:** Marcos Soto
- **Related:** [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [ADR-0035](0035-findings-and-manual-maturity-assessment.md),
  [ADR-0042](0042-a-repository-brings-its-own-gate.md),
  [ADR-0046](0046-a-comparison-states-what-it-is-valid-for.md),
  [ADR-0048](0048-a-check-that-did-not-measure-is-not-a-failure.md),
  [ADR-0056](0056-cases-are-matched-against-runner-capabilities.md)

## Context

Two cases were written from the talos audit and run in two experiments each — four
runs, four arms, about $5 and two hours of wall clock. Every number they produced was
about the cases themselves, and nothing in any result said so.

**Case 1** validated with `for t in {workspace}/tests/test_*.sh; do bash "$t"; done`.
A shell `for` loop exits with the status of its *last* command, so fifty-four failing
scripts ahead of one passing script is a green check. Both arms recorded
`task_validation_pass = 1` and `test_pass_rate = 1.0`, while the same run's stored
detail read `rc=2 talos/runner: CONFIG ERROR`. The failures had a single cause —
`Dockerfile.runner` shipped no `jq`, and talos reaches for it in 76 files — and the
agent's own `apt-get install jq` lived in the *agent's* container, which the later
validation container does not inherit.

**Case 2** validated with `bash benchmark/model/run-model.sh` (no `{workspace}`) and a
Python one-liner of the shape `any(p in text for p in patterns)`. The first resolved
against the lab's own repository root, exited 127, and never executed the costly-model
scenario the case existed to check — the file was there; the path was not. The second
matches any README containing the word "cost", so it cannot fail. `test_pass_rate = 0.5`
in both arms is that pair of artifacts, not a finding.

Neither case carried a `scoring_rubric`. `_llm_judge_rubric` returns at its first guard
when the rubric is empty — silently — so all four runs parked in
`awaiting_manual_score` with a NULL score. That read as a scoring backlog. It was a
case defect.

Meanwhile the runs themselves were being told the wrong thing. `bench_run.py` decided
"is this SWE-bench?" by calling `spec_provisions_repo()`, which answers a different
question: *does a repository have to be cloned first?* `source: "git_target"` is
stamped on **every** case paired with an external target (ADR-0029/0030), so from step
2 onward both talos cases were handed a continuation prompt telling them to resolve a
GitHub issue, listing an empty set of "target failing test(s)", and instructing them to
run `python -m pytest` in a repository with no Python. Both arms got it. The arm with
more governance to reconcile reacted worse — 6.6× the turns, 9× the cost, zero diffs —
and that difference would have been reported as a treatment effect.

The lab was shaping every result it produced. The point of the lab is to audit
*different* repositories; talos was only the first.

## Decision

**A case is refused at the moment it is written when its checks could not measure the
agent's work.** `case_validation.py` holds the rules, `case_service.create_case` and
`update_case` enforce them, and the API answers 422 with one message per offending
command — each naming the command and, where a mechanical fix exists, the corrected
command itself. The rules are narrow by construction: they reject shapes that are wrong
under every reading and stay quiet about anything arguable.

- a loop that swallows its failures (no `|| exit 1`, `set -e`, or `&&` guard);
- a repository path with nothing to anchor it (no `{workspace}`, `{run_id}`, `/app`,
  or `-C`), which resolves against the lab's own root;
- a check that cannot fail (`true`, a bare `echo`, `test -e {workspace}`, a Python
  predicate over its own literals with no file read);
- a multi-line entry, which the one-command-per-line storage would split;
- a command that does not look runnable at all;
- and, for any case that carries commands, a missing scale to grade against.

**A case proves it can fail before it is allowed to run.** `POST /api/cases/{id}/dry-run`
executes the case's own checks against the target's clean, pinned tree with no agent
work applied, through the same hardened lane ADR-0056 defined. At least one check must
fail conclusively; a case whose checks all pass there is recorded
`dry_run.verdict = "tautological"` and `experiment_service` refuses to launch it. A
check that timed out or a checkout that broke is `inconclusive` and claims nothing
(ADR-0048).

**A `validation_hint` is a command or a written check, never both.** The audit's system
prompt now demands one or the other, and `normalize_hint` splits what arrives anyway:
the first backticked span becomes the command when it is one, the rest becomes the
note. Both travel on the case plan, and "Draft this as a case" carries the command *as
a command* instead of flattening the structured fields back into a sentence for a model
to re-derive.

**"Needs a clone" and "is SWE-bench" are separate predicates.** `spec_is_swe_bench`
answers the second from the case type or the presence of `fail_to_pass`, and nothing
else. A SWE-bench spec that names no failing tests now fails the run loudly rather than
emitting an empty objective. Each step records which template wrote its prompt in
`usage.json` as `prompt_kinds`, so this class of defect is readable from the artifacts
instead of only by opening a step prompt by hand.

**Where a harness lives is one taxonomy.** `governance_paths.py` replaces four drifted
copies. Dotted roots (`.claude/`, `.agents/`) are trusted; a bare root has to prove
itself with the marker that makes it unambiguous — `skills/` needs a `SKILL.md`,
`hooks/` needs a script-shaped file. talos has sixteen skills and four hooks at the
root and the audit had reported it as having no harness at all, which is worse than
"unknown" because it reads as a finding.

**A silence nobody can attribute is not the agent's fault.** The watchdog's log markers
are per harness. A `claude` run now prints `# awaiting model` when a tool result goes
back to the model — the one moment it announces a request in flight — and a harness the
lab has not been taught resolves the ambiguity conservatively: unattributable silence
is a lab failure, excluded from the leaderboard, never an agent stall.

**An experiment closes itself.** `ExperimentStatus.complete` existed in the enum and was
assigned by no line of the codebase. `try_complete` sets it once every run has reached a
terminal state, and queues the reading — whose single gateway call is now retried on a
transport error and *not* retried on a 4xx.

## Consequences

- A case that would have produced a false green cannot be stored, and one that would
  have produced an unscored run cannot either. Both were previously invisible.
- The dry run costs one clean checkout per case. That is the price of knowing a check
  can fail, and it is paid once, before any model is called.
- `is_executable_command` now also accepts a command whose leading binary names a
  runtime, so `git -C /app diff --quiet` and `jq -e .` stop being silently dropped by
  `build_validation_spec`. Whether the runner *has* that runtime stays a separate
  question, answered by ADR-0056's capability match.
- Existing cases are not migrated. The rules bite on create and update, the dry run is
  opt-in per case, and the launch gate refuses only a case whose dry run actually said
  `tautological` — a known defect, not a missing ceremony.
- `governance.py`'s reading surface widened, so `RUNNER_GOVERNANCE_DIRS` widened with
  it. The tripwire in `test_executors.py` is what caught the gap and is what keeps the
  two aligned.
- The two talos experiments remain in the corpus and remain unreadable as evidence
  about talos or about the lab's template. They are kept as the record of why these
  rules exist.

## Alternatives considered

**Warn instead of refusing.** A warning on a page nobody re-reads is how the two cases
got written in the first place. The failure mode here is silent and expensive, and the
fix is always one edit away while the author is still on the form.

**Only the dry run, no static rules.** The dry run needs a pinned target and a clean
checkout, so it cannot answer for a case that has neither, and it costs a clone. The
static rules catch the shapes cheaply; the dry run catches what no heuristic can.

**Only the static rules, no dry run.** No heuristic can tell whether a check happens to
pass on this particular tree. Tautology is a fact about a case *and its repository*,
and only running it says so.

**Widen `_EXEC_HINTS` for `jq` and `git`.** A second hand-maintained vocabulary beside
the capability table would drift the same way the four path taxonomies did. Consulting
the table is one rule and it cannot fall behind.

## Follow-up this does not close

- The dry run has no UI of its own: the verdict is stored on the case and surfaced when
  the experiment refuses to launch. A badge on the case would say it earlier.
- Cases already in the database were written under the old contract. Nothing sweeps
  them; the first edit or dry run is when each one is held to this.
- `prompt_kinds` is recorded but nothing reads it yet. The experiment audit is the
  obvious consumer: an arm whose steps were framed differently is not comparable.
