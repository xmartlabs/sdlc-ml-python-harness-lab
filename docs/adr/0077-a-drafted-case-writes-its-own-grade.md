# ADR-0077: A drafted case writes its own grade, and says what it imports

- **Status:** Accepted
- **Date:** 2026-11-13
- **Deciders:** Marcos Soto
- **Related:** [ADR-0057](0057-a-case-is-validated-before-it-can-measure-anything.md),
  [ADR-0071](0071-a-grader-brings-its-own-dependencies-and-never-the-agents-tests.md),
  [ADR-0076](0076-per-step-reward.md),
  [case formation](../guide-case-formation.md#what-the-case-assistant-does-with-these-rules)

## Context

ADR-0071 set two rules for a case graded by tests: the lab ships the tests (never the
agent's), and the case declares what those tests import. Both rules were written for
cases authored by hand in `task.yaml`. The case assistant, which drafts a case from one
sentence, was never brought in line:

- **Its prompt asked for the pattern ADR-0071 forbids.** It told the model to prefer
  `pytest {workspace}/test_*.py` and, when the agent must produce a test file, to "have
  the instruction ask for it and run it". `walletsystem-concurrent-deposits` was drafted
  that way and still grades with `pytest {workspace}/test_wallet_system.py`.
- **A draft could not declare dependencies.** Neither the draft, the Cases form nor the
  case editor had a field for `validation_requirements`. Rerunning
  `eda-ml-iris-categ-numerico` on 2026-11-13 (K-003) left its pytest check inconclusive
  on `No module named 'numpy'` in the runner, exactly as in ADR-0071.
- **An edit erased what a case had declared.** `update_case` assigned every field of the
  payload, and the editor sent no `metadata_json`, so the first edit of a case wiped its
  `validation_requirements`, `starter_dir` and a SWE case's instance data.
- **Per-step reward needs resolution.** ADR-0076 grades each step as the share of
  checks passing. One all-or-nothing command shows every step as 0 or 1.

Moving the assistant to inline checks surfaced two latent defects. Commands were rendered
with `str.format`, so a check carrying its own braces (`d={'a': 1}`) was accepted on save
and then failed every run with `KeyError`. And the tautology rule refused any `python -c`
predicate that read no file, including one over what the agent's own module returned. A
real draft for an Iris pipeline lost its EDA check to it.

## Decision

1. **The assistant writes the grade; it never runs the agent's tests.** Without a target
   repository the workspace starts empty, so a drafted command that runs a test suite
   inside `{workspace}` is dropped with its reason (`check_drafted_command`). The prompt
   asks for inline checks that import the agent's module and assert one behaviour. A
   draft with a `target_id` keeps the repository's suite, even when its checkout could
   not be read. The rule applies to drafts only. On save, a hand-written case whose tests
   come from a starter cannot be told apart from one that runs the agent's tests, so the
   editor does not guess.
2. **A draft declares what its checks import.** `validation_requirements` is part of the
   draft and goes through the same filter as the setup step (`requirement_is_installable`).
   It is shown and editable as **Grader requirements**, and saved under
   `metadata_json.validation_requirements`.
3. **Metadata is merged, key by key.** On create and update, a key the payload names
   replaces the stored one, an empty value removes it, and keys it does not name are
   kept. A form can then own one key without knowing the rest.
4. **One check per behaviour.** The assistant is asked for 3 to 10 checks, ordered from
   the most basic to the most demanding, so per-step progress has resolution.
5. **Only `{workspace}` and `{run_id}` are placeholders.** Every command (validation, gate
   check, dry run, target setup) is rendered by `render_command`. Other braces are left
   as written, and `{{`/`}}` still mean one brace.
6. **A predicate over the agent's code is a check.** A `python -c` one-liner that names
   `{workspace}` and reaches into it (`sys.path`, `importlib`, `runpy`, `subprocess`) is
   no longer tautological. `import sys` alone does not count.

## Consequences

- New drafts grade the agent with checks the lab wrote, and install what those checks
  import. A drafted ML case now carries its `pandas`/`scikit-learn` pins.
- Existing cases are not rewritten. `walletsystem-concurrent-deposits` still grades with
  the agent's tests and should be rewritten before it is used in an experiment;
  `eda-ml-iris-categ-numerico` is already superseded by `eda-iris-pipeline-v2`.
- The assistant's pins are a model's guess. The dry run and a first run with a strong
  model are still what prove a drafted case can be graded.
- A case's grade is now longer to read (several inline checks instead of one `pytest`).
  That is the price of the per-step curve.

## Alternatives considered

- **Refuse workspace test suites on save, for every case.** Rejected: the editor cannot
  tell a starter's or the lab's tests from the agent's, and would refuse valid cases.
- **Have the assistant ship a test file under `benchmarks/<case>/tests/`.** That is the
  hand-written path and it remains the best one, but a draft is stored in the database,
  not the repository, and the tamper guard compares shipped tests with `HEAD`.
- **Escape braces when saving.** Rejected: it would change the stored text the person
  typed, and every reader of a command would have to unescape it. Rendering only the two
  placeholders fixes it at the one place commands become shell lines.
