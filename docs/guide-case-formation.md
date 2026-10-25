# Forming a case that measures the agent

**A case that cannot fail is not a measurement, and nothing in a result says so.** A
false green looks exactly like a real one — same field, same number, same colour. This
page is the set of rules that keep one from being written, and the one experiment that
proves a case can fail before it is allowed to run.

Decision record: [ADR-0057](adr/0057-a-case-is-validated-before-it-can-measure-anything.md).
Whether the runner *can* execute a case at all is a different question, answered by
[ADR-0056](adr/0056-cases-are-matched-against-runner-capabilities.md) and
[the capabilities guide](guide-runner-capabilities.md).

## Where the checks are

| Layer | What it catches | Where |
|---|---|---|
| The audit's hint contract | prose and command mixed into one field | `repository_audit_service.normalize_hint` |
| The drafter's screen | a model's command the lab would refuse | `case_draft_service.screen_commands` |
| Creation and edit | every shape below, as a 422 | `case_service.create_case` / `update_case` |
| The dry run | a case that happens to pass on this repository | `POST /api/cases/{id}/dry-run` |
| The launch gate | an experiment on a case already known to be tautological | `experiment_service._refuse_tautological_cases` |

The rules themselves are pure and live in one module: `case_validation.py`.

## The six refusals

Each one is a shape that produced a wrong number in a real experiment.

**1. A loop that swallows its failures.**

```sh
# refused — exits with the LAST script's status
for t in {workspace}/tests/test_*.sh; do bash "$t"; done
# accepted
for t in {workspace}/tests/test_*.sh; do bash "$t" || exit 1; done
```

Fifty-four failing scripts ahead of one passing script is a green check. The refusal
carries the corrected loop, not advice.

**2. A repository path with nothing to anchor it.**

```sh
# refused — resolves against the lab's own root, exits 127
bash benchmark/model/run-model.sh
# accepted
bash {workspace}/benchmark/model/run-model.sh
```

`{workspace}`, `{run_id}`, an absolute path, `/app` or `-C` all count as anchors. The
file existing is not the point: the path is what has to say where it is.

**3. A check that cannot fail.** `true`, a bare `echo`, `test -e {workspace}` (the
workspace is created before validation runs), or a Python one-liner whose predicate is
a literal it carries itself:

```sh
# refused — matches any text containing one of its own patterns
python3 -c "patterns=['cost']; import sys; sys.exit(0 if any(p in text for p in patterns) else 1)"
```

**4. A multi-line entry.** The case stores one shell command per line, so a heredoc or
a script body is split and each line is run on its own — the first often exits 0.

**5. A command that does not look runnable.** Prose in the commands field is not a
check. `build_validation_spec` used to drop it without saying so.

**6. Commands with no scale to grade against.** A case carrying validation commands
needs a `scoring_rubric` or `success_criteria`. Without one `_llm_judge_rubric` returns
at its first guard and every run of the case parks in `awaiting_manual_score` with no
score and no record of why — which reads as a scoring backlog rather than as the case
defect it is. SWE-bench cases are exempt: their score is the objective verdict.

## The dry run

```
POST /api/cases/{case_id}/dry-run
```

Runs the case's own checks against the target's **clean, pinned tree** — cloned,
set up, and with no agent work applied — through the hardened validation lane
(no lab secrets, no network past provisioning). Three verdicts, stored on the case as
`metadata_json.dry_run`:

| Verdict | Meaning |
|---|---|
| `ok` | at least one check failed conclusively on the untouched tree — the case has something to measure |
| `tautological` | every check passed before the agent started; its green means nothing, and the experiment gate will refuse to launch it |
| `inconclusive` | provisioning broke, or a check settled nothing — it timed out, was killed, or its command did not exist (exit 127, which used to read as a false `ok`) — so nothing is claimed ([ADR-0048](adr/0048-a-check-that-did-not-measure-is-not-a-failure.md), [ADR-0062](adr/0062-a-check-the-environment-failed-is-not-a-verdict.md)) |

Every check is asked, even after one fails: the question is whether *any* of them can
fail, and stopping at the first answer leaves the rest unasked.

## The hint contract

A `validation_hint` from a repository audit is one of two things, never both:

- a single bare shell command, relative to the repository root, no prose and no
  backticks — `bash tests/run.sh`;
- a one-sentence written check that starts with "Comprobar que" and invents no command.

A model shown a repository full of markdown writes the command in backticks anyway, so
`normalize_hint` splits what arrives: the first backticked span becomes
`validation_command` when it is one, the surrounding prose becomes `validation_note`.
Both travel on the case plan, and "Draft this as a case" carries the command *as a
command* — the button used to flatten title, reason and hint into one sentence and ask
a model to recover the command from it.

## Which prompt a run was given

`usage.json` records `prompt_kinds`, one entry per executed step:

| Value | Template |
|---|---|
| `case` | the case's own prompt (step 1, or a single-step run) |
| `swe` | the SWE-bench continuation — only for a SWE-bench case |
| `case_step` | an authored `step_prompts` entry |
| `continuation` | the curated benchmark (Tetris) continuation |
| `generic` | the neutral continuation, for everything else |

"Needs a repository cloned" and "is a SWE-bench task" are separate predicates
(`spec_provisions_repo` and `spec_is_swe_bench`). Reading the first as the second gave
every case paired with an external repository a prompt about resolving a GitHub issue.
`prompt_kinds` is what makes that class of defect readable from the artifacts.

## The grade is the lab's, and so are its dependencies

[ADR-0071](adr/0071-a-grader-brings-its-own-dependencies-and-never-the-agents-tests.md)
sets two rules for any case graded by a test suite:

- **Ship the tests; never grade the agent's.** Put the suite under
  `benchmarks/<case>/tests/`, guard it against edits, and run one command per test. Asking
  the agent to write tests is fine — they are part of the work — but a case that runs the
  file the agent wrote lets six easy tests outscore twenty-five demanding ones.
  `eda-ml-iris-categ-numerico` did exactly that; `eda-iris-pipeline-v2` is its fix.
- **Declare what the tests import.** Validation runs offline in a clean container. List
  the third-party packages the graded tests need, pinned, under `validation_requirements`
  in `task.yaml`:

  ```yaml
  validation_requirements:
    - pandas==3.1.0
    - scikit-learn==1.9.1
  ```

  A setup step installs them with network into the run's folder before the checks run.
  If it cannot, the checks are recorded inconclusive rather than failed; the same goes for
  a check that dies on `No module named` for a module the agent was not asked to write.
  Pin the versions you calibrated on.

Every graded command `cd`s into the workspace first, so pass the tests an absolute path —
`CLINIC_WORKSPACE="$PWD"`, never `CLINIC_WORKSPACE={workspace}`, which is relative to the
lab root and stops resolving after the `cd`. A test holds every `task.yaml` to this.

## Frontend cases — graded in a real browser

A frontend case is graded the way a user meets the page: in headless Chromium,
through roles, accessible names, `data-testid` hooks and the keyboard — never
through class names or the DOM's shape, which an agent is free to choose
([ADR-0067](adr/0067-a-frontend-case-is-graded-in-a-real-browser.md)).
`benchmarks/todo-web-v1` is the template:

- **The contract is in the prompt.** A table of what the tests look for — the
  heading text, the input's accessible name, `data-testid="todo-item"`, the
  filter buttons and their `aria-pressed`. A test that relies on something the
  prompt does not name measures guessing, not frontend skill.
- **Offline and server-less.** The page is opened from `file://`, so validation
  needs neither a dev server nor network, and runs in the network-less
  validation lane.
- **One command per test**, after the tamper guard and an entry-point check, so
  `test_pass_rate` has the suite's resolution.
- **Exact locators.** `get_by_role("button", name="Completed")` also matches
  *Clear completed*; use `exact=True` for short names, or a correct page fails
  a test it should pass. This was caught by running a reference
  implementation, not by reading the tests.
- **Calibrate before trusting.** An empty workspace must fail every test, a
  reference implementation (kept *outside* the repository) must pass every
  test, and a plausible weaker build should land in between — `todo-web-v1`
  scores 0, 12 and 9 of 12.

The runner provides Python Playwright, `pytest-playwright` and Chromium from a
pinned layer of `Dockerfile.runner`; rebuild the image (`make app-up`) after
pulling.
