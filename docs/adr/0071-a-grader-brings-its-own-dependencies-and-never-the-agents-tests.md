# ADR-0071: A grader brings its own dependencies, and never the agent's tests

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Marcos Soto
- **Related:** [ADR-0062](0062-a-check-the-environment-failed-is-not-a-verdict.md),
  [ADR-0065](0065-the-audit-decides-what-code-can-decide.md),
  [ADR-0066](0066-harness-and-model-vary-together-only-in-a-full-grid.md),
  [ADR-0069](0069-a-model-is-judged-by-the-tier-it-does-reliably.md)

## Context

Experiment `do-stronger-models-need-less-harness-2x3` (2026-10-23) ran the case
`eda-ml-iris-categ-numerico` on claude-opus-5-5 and claude-haiku-4-5 at three harness
levels. Every run that finished scored 0 or 3 out of 10. None of it measured a model:

- **The grader could not import its own subject.** Validation runs offline in a clean
  runner container, which has no pandas or scikit-learn. The agent had installed them in
  *its* container, so its own tests passed there; the case's `pytest` then failed at
  collection, exit 2, in ten runs of ten. `ModuleNotFoundError: No module named
  'joblib'` was recorded as a conclusive failure of the agent, and the LLM judge, told
  "the authoritative pytest check failed", capped every score.
- **The grade was the agent's own tests.** The prompt asked the agent to write
  `test_pipeline_iris_eda.py`, and validation ran that file. A run that wrote six easy
  tests could pass where one that wrote twenty-five demanding ones and failed one could
  not. Re-run with their dependencies, all six Opus runs passed their own tests, and the
  Haiku runs failed one each — of suites of different sizes, written by the subject.
- Two runs died for the lab's reasons: a `venv/` the agent created made `git diff`
  return binary that strict UTF-8 decoding rejected, and the gateway's origin went down
  behind Cloudflare (521) thirteen turns into a run.

## Decision

1. **A case declares what its graders import.** `validation_requirements` in
   `task.yaml` — pinned pip requirements — is stored in the case's metadata. Validation
   gains a **setup step** that installs them with network into
   `data/runs/<id>/validation-deps/` (uv first: the runner's venv ships no pip), and
   every offline check runs with that directory on `PYTHONPATH`. The workspace's
   `requirements.txt` is never read for this: the agent wrote it. A requirement that is
   not a plain name-and-version is dropped, never quoted into a shell.
2. **A grader that could not be equipped measured nothing.** When the setup step fails,
   the checks are recorded inconclusive (`missing_dependency`) without running. When a
   check fails on `No module named 'x'` and `x` is not a module of the workspace, that
   check is inconclusive too — the judge already treats inconclusive checks as neither
   confirming nor refuting a rubric level. A missing module the agent was asked to write
   remains its failure.
3. **The lab grades with its own tests.** A case whose acceptance is a test suite ships
   that suite under `benchmarks/<case>/tests/`, guarded against edits, one validation
   command per test, as the clinic and frontend cases already do. The agent may — and is
   asked to — write tests of its own; they are part of the work, not the grade.
   `eda-iris-pipeline-v2` replaces `eda-ml-iris-categ-numerico` on this basis, with
   tests calibrated on a reference solution (12/12), an empty workspace (0/12) and two
   partial ones (11/12, 10/12). The reference solution is not in the repository.
4. **A graded command hands its tests an absolute workspace.** Every graded command
   `cd`s into `{workspace}` first, so a lab-relative `{workspace}` passed on no longer
   resolves after it. Commands pass `"$PWD"`; a test holds every `task.yaml` to it.
5. **The archive survives what an agent installs.** `diff.patch` leaves out
   environments and caches (`venv`, `.venv`, `node_modules`, `__pycache__`, …) — they
   stay in the archived workspace — and git output is decoded with replacement, never
   strictly.
6. **A server-side error is the endpoint's.** `API Error: 5xx` from the provider or a
   proxy in front of it is labelled `endpoint down` and counted as unmeasured.

## Consequences

- Rerunning the 2026-10-23 experiment on `eda-iris-pipeline-v2` measures the models:
  on the archived code of those runs, Opus passes 12/12 and Haiku 10–11/12.
- The setup step needs network and time (about a minute for the scientific stack, once
  per run). Cases with no `validation_requirements` are unchanged.
- The tamper guard compares the shipped tests with `HEAD`, so a new case's checks pass
  only once its tests are committed.
- `eda-ml-iris-categ-numerico` lives only in the database. It is not deleted — its runs
  stay readable — but it should not be chosen for new experiments.
- Not done: retrying a run whose gateway went down mid-way. A retry would repeat the
  spend of every completed turn; the run is marked unmeasured and **Run again** resumes
  it on request.
