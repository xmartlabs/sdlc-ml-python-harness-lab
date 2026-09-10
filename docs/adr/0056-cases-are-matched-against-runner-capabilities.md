# ADR-0056: Cases are matched against runner capabilities, not the repository's language

- **Status:** Accepted
- **Date:** 2026-09-09
- **Deciders:** Marcos Soto
- **Related:** [ADR-0002](0002-celery-docker-per-run-execution.md),
  [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [ADR-0035](0035-findings-and-manual-maturity-assessment.md),
  [ADR-0048](0048-a-check-that-did-not-measure-is-not-a-failure.md),
  [ADR-0054](0054-a-repository-is-audited-before-the-lab-runs-anything-on-it.md)

## Context

ADR-0054 gave the wizard a gate decided from facts, and the runtime half of that gate
asked one question about the whole repository: is the dominant language one the runner
image ships. Two real repositories showed that this question is the wrong one.

**talos** is Shell. Its tests are hermetic, its commands are real and verifiable —
`for t in tests/test_*.sh; do bash "$t"; done`, `bash benchmark/run.sh` — and the
runner image has executed bash all along: `python:3.11-slim` ships bash 5.2.37 with its
Debian base, and nothing in `Dockerfile.runner` had to be added for that. talos was
stopped by a classification table, not by a missing toolchain.

**xl-skills-collection** is also Shell-classified, and almost every case worth
proposing there needs no execution at all: does the right `AGENTS.md` apply in this
directory, does a kit ship a README, is the Figma-to-code rule respected. Those cases
are settled by reading. They were blocked by a runtime they never needed.

The two failures share one cause: **the language of the tree was being used as the
execution requirement of every case**. It is neither. A Shell repository can propose a
case that runs `pytest`; a Python repository can propose a case that only checks
whether a file exists.

## Decision

**Four concepts, and only one of them decides whether a case runs.**

- *Repository ecosystem* — what the tree is written in. Unchanged
  (`readiness_service.detect_ecosystems`). It describes the repository; it no longer
  answers for the cases.
- *Runner capabilities* — what the runner can execute, as
  `{runtime, available, version, executable, source}` per runtime
  (`runner_capabilities.py`). `source` is `declared` (the image's build recipe) or
  `probed` (the image answering for itself). A capability that was never probed does
  not claim a version, in the spirit of ADR-0048: a version nobody measured is a guess
  dressed as a fact.
- *Case requirements* — derived from the command, never stored. `runtime_for_command`
  reads the leading binary, stepping over the wrappers a validation command carries
  (`timeout N`, `FOO=1`, `cd … &&`, `for … do`). Storing a requirement would let a row
  disagree with the command beside it.
- *Capability matching* — `classify_case` gives each proposed case one of three
  statuses, and the order it decides them in is the whole point:
  **static** (the hint is not an executable command, so no runtime can block it),
  **runnable** (the required runtime is available), **blocked** (it is not). Blocked is
  a statement about one case; its siblings are classified on their own terms and run.

**Execution coverage replaces the repository-wide yes/no.** `none`, `partial`, `full`
over the proposed cases, reported with the counts behind it. `runner_can_execute` keeps
its old name and its old meaning — can the runner execute the repository's *dominant*
language — because that is still an honest answer to its own question. It just no
longer decides anything for the cases.

**The gate is now two stages, because of when the model speaks.** `collect_evidence`
and therefore `next_step` run before the LLM proposes a single case, so per-case
classification cannot feed the gate in one pass.

- *Stage A, pre-LLM and deterministic:* `runtime_verdict` asks the capability table
  instead of a hardcoded extension set, so `.sh` is a supported extension exactly
  because bash is a capability. This alone lets talos and xl-skills through; Go and
  COBOL trees still stop.
- *Stage B, post-LLM:* `plan_cases` classifies the proposals and `apply_case_plan`
  folds the result back, permitted to make **exactly one** transition —
  `unsupported_runtime → continue`, and only when `runnable + static > 0`. Never the
  reverse, and never over `no_harness`. A model that proposes five beautiful cases for
  a repository containing nothing written for an agent has not created a harness. Facts
  close doors; the plan may reopen only the one door a fact closed for a reason that
  turned out not to apply (ADR-0035).

**A version requirement can only degrade a verdict.** `compare_runtime_version` returns
`exact`, `partial` or `unknown`, and `bash 3.2` against `bash 5.2.37` is `partial` —
executable yes, target runtime exact no. Nothing in the reporting path emits a word
like *certified* for a partial match. A model-supplied version is clamped to
`^\d+(\.\d+){0,2}$` and cannot promote a blocked case to runnable.

**The interpreter follows the command, not the repository.** `/bin/sh` in the runner
image is dash, so a command using `[[`, `<(`, `pipefail`, `local` or `declare` is
handed to `bash -c`; everything that ran under `sh -c` before still does. Only
`argv[0]` varies, and `-c` — never `-lc` — for the reason it always was: a login shell
resets `PATH` and would drop `/opt/venv/bin`.

**The validation lane is hardened, which this change made overdue.** That lane runs an
audited repository's own commands (ADR-0029) and never calls a model, and it was
receiving the entire lab `.env` — `GATEWAY_TOKEN`, GitHub tokens, Langfuse keys —
through `build_run_environment()`. It now gets `sanitized_run_environment()`: no `.env`,
no `.claude/settings*.json`, no host environment, no `PATH` handed into a container.
Plus `cap_drop=ALL`, `no-new-privileges`, a pids limit, a memory limit, a 2 MiB output
cap, the existing mandatory timeout, and `network_mode="none"` **on `validate`-phase
commands only** — `provision` and `setup` (`git clone`, `npm ci`, `uv sync`) keep their
egress, which is what the existing phase tag is for.

**The agent lane is untouched.** The container running the agent keeps
`build_run_environment()`, the full `.env`, `extra_hosts`, its network and
`IS_SANDBOX=1`. That is where the model is called; the validation lane never calls one.

## Consequences

- A Shell repository reaches the case step. talos reports `Python ✓ Node ✓ Bash ✓`,
  its three bash commands runnable, and `bash 3.2` versus `5.2.37` as partial.
  xl-skills reports mostly static cases and continues.
- Adding a runtime later is registering a capability and a command mapping. Go, Java,
  Rust and Docker would each be two table entries plus whatever the image needs — not
  another audit rewrite.
- No schema change. Requirements are derived, and this repository has no Alembic:
  columns are hand-rolled `ALTER TABLE` blocks in `db.py`.
- Audits recorded before this change have no `case_plan` and no `capabilities`; the
  report and the wizard render them exactly as they did.
- SWE-bench Pro's `local` specs are deliberately **not** hardened: they shell out to
  docker themselves against the worker's socket, and stripping their environment would
  strip the very thing that lets them reach the daemon.
- Blocked cases are visible rather than invisible. A repository can now show honestly
  that three of its five cases run and two do not.

## Alternatives considered

- **A `CaseExecutor` hierarchy per runtime.** Rejected: `executors.RunExecutor` /
  `RunHandle` already abstracts execution, and the only thing that varies per runtime
  is `argv[0]`. A class hierarchy with one differing method is a layer, not a design.
- **A `CaseRequirements` model with a database column.** Rejected: a stored requirement
  can drift from the command beside it, and it would need a migration in a repository
  that has no migration tool.
- **Widening `_EXEC_HINTS` so more hints count as executable.** Rejected: that set
  decides what runs for every existing case. Static is decided by the same
  `is_executable_command` the runner uses at run time; a kinder heuristic here would
  let the audit promise a check the runner then silently skips.
- **Keeping the repository-wide gate and special-casing Shell.** Rejected: it is the
  request the evidence argues against, and the next runtime would need the same patch.

## Follow-up this does not close

`DockerRunExecutor` bind-mounts the whole lab repository read-write at `/app`, so
dropping the validation container to a non-root `user=` would buy little containment
while breaking `__pycache__`, `.pytest_cache` and npm cache writes under Linux DooD.
The containment worth having is a narrower mount, not a narrower user. Recorded here
rather than half-applied.
