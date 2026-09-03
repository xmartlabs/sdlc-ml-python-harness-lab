# ADR-0040: Validating a tool is its own mode, not a harness evaluation

- **Status:** Accepted
- **Date:** 2026-09-03
- **Deciders:** Marcos Soto
- **Related:** [ADR-0028](0028-evaluation-modes.md),
  [ADR-0033](0033-experiment-above-run.md),
  [ADR-0034](0034-score-registry.md),
  [ADR-0038](0038-every-run-leaves-with-a-score.md)

## Context

[ADR-0028](0028-evaluation-modes.md) declared three modes, and every one of them asks
a question *about the harness*: does this skill change the outcome (`harness_evaluation`),
does the harness hold outside the repository it was designed in (`cross_repo`), what
does this repository already do (`discovery`).

A different question keeps arriving first. Someone builds a thing — an agent served
behind a local API, an MCP server, a service wrapper — writes a skill that drives it,
and wants to know whether **the thing works reliably** when a real agent drives it
across harnesses and models. Same machinery: a case, arms, repetitions, objective
validation, a sandbox to look inside a run. Different question, and different rules
about what may be concluded.

None of the three modes names it. It is not `harness_evaluation` — the harness is held
constant and nothing is ablated; the subject under test is the thing the skill calls.
It is not `cross_repo` and it is not `discovery`. Running it under
`harness_evaluation`'s label invites exactly the conclusion this lab exists to refuse:
reading "the tool passed 8 of 9" as evidence about the harness, when no arm was
ablated and there is no baseline to subtract.

It is also the only question in the lab that needs no prerequisites — no registered
target repository, no existing history — and it *produces* what the other modes
consume: a staged skill and a case with declared, objective validation.

## Decision

We add a fourth declared mode, **`subject_validation`**, with its own validity rules.

**The subject is declared, not inferred.** `Experiment` gains a `subject` field: the
name of what is under test. Deriving it from the arms' overrides would be the
reconstruction [ADR-0033](0033-experiment-above-run.md) rejects — an arm that could
only be recovered, never declared. It is empty under every other mode, including for
every experiment created before this ADR, which is what they were.

**There is no control arm, by construction.** Every arm is a peer; none is the
baseline of another. `validate_design` returns before the exactly-one-control check,
the way `discovery` does. A single arm is a valid design — "does it work under my
setup?" is a legitimate question — so the mode has no "must vary something"
requirement either.

**Two designs are refused outright.** Varying `target_repo_id` makes the sentence the
mode licenses unreadable ("passed 8 of 9" — in which repository?); that question is
`cross_repo`. A non-empty `skills_disabled` is ablation, which is
`harness_evaluation`'s question, and allowing it here builds a harness evaluation
wearing this mode's label. Varying harness *and* model together only warns: the cross
product is desirable here, but each cell is its own population.

**A case with no executable validation command is refused at creation.** Not warned —
refused. `build_validation_spec` silently skips entries that do not look executable,
so a case whose only "validation" is prose yields zero commands, `parse_objective`
returns `passed=None`, `task_validation_pass` is never emitted, `scored_runs` is 0,
and the pass rate is `None`. The experiment runs in full, costs real money, and
answers nothing without saying so. The check reuses `_looks_executable`, the same
predicate that decides what the runner will actually execute.

**The verdict reports a rate per arm and never a pooled one.** `verdict()` returns
before any delta arithmetic with `causal: False`, a claim that names the subject and
states that the experiment supports no claim about the harness, and one highlight per
arm carrying `successes / scored_runs`. Averaging the arms together would describe a
configuration nobody runs.

The sentence this mode licenses, and the only one, is: **this subject passed N of M
under `<harness>`/`<model>`.**

## Consequences

The mode becomes the lab's on-ramp: the first card on the home page, and the only
door that opens on a fresh instance. `cross_repo` keeps its mode and its validity
rules but loses its home-page card — it moves to the Repositories page, where it
becomes available exactly when a second repository exists. Two doors that cannot be
opened are worse than one that works; removing the *card* narrows nothing about what
the instrument can conclude, whereas removing the *mode* would.

The score itself is not new. `task_validation_pass` (`fact`, deterministic),
`ConditionSummary.success_rate` denominated in scored runs, the per-case grid, and
`repetition_warning` all predate this ADR. What is new is framing and gates.

Cost accepted, twofold. First: a fourth set of validity rules to implement and keep
honest, in a codebase where the rules are the product. Second, and larger — **the
mode's number is only as good as the command that produces it.** A weak validation
command yields a confident 100% that measures nothing: a smoke check that the tetris
game starts cannot fail once the game starts, so an arm that passes it has
demonstrated only that it did not crash. The gate can force a command to exist and to
be executable; it cannot force it to be able to fail. That judgement stays with the
person declaring the case, and the guide says so where the case is written.

## Alternatives considered

- **Run it as `harness_evaluation` with a single arm.** Rejected: it is the failure
  this ADR exists to prevent. The mode is the thing that licenses conclusions, so a
  mislabelled experiment produces a correctly computed number attached to a claim the
  data does not support — the exact defect [ADR-0028](0028-evaluation-modes.md)
  identified, reintroduced through the label instead of through the display.
- **Call the mode `tool_validation`.** Rejected: "tool" underestimates the scope. The
  subject is as often an MCP server, an internal API, or a service wrapper as it is a
  tool, and naming the mode after the narrowest case invites people with the other
  cases to look for a mode that does not exist.
- **Warn instead of block on a missing validation command.** Rejected: the failure is
  silent and downstream. By the time the pass rate reads `None`, the runs have already
  executed and been paid for, and the empty result looks like a bug in the lab rather
  than a case that never declared what success was.
- **Derive the subject from the arms' overrides.** Rejected by
  [ADR-0033](0033-experiment-above-run.md): what the experiment is about is declared
  at creation or it is a guess made at read time.
