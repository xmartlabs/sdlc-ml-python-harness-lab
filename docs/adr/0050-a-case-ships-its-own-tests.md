# ADR-0050: A case ships its own tests

- **Status:** Accepted
- **Date:** 2026-09-08
- **Deciders:** Marcos Soto
- **Related:** [ADR-0034](0034-score-registry.md),
  [ADR-0038](0038-every-run-leaves-with-a-score.md),
  [ADR-0047](0047-a-case-exposes-two-prompt-variants-the-task-and-the-task-plus-the-harness.md),
  [ADR-0048](0048-a-check-that-did-not-measure-is-not-a-failure.md)

## Context

The first genuinely bare-vs-full-harness experiment on `tetris-v1` was invalid for a
reason no harness change could fix: the case has **one** validation command (a
headless smoke run), so its only objective outcome is binary, and Claude Haiku passes
it bare. Above that floor the grade came from an LLM judge reading a diff against a
rubric whose levels 3-10 describe gameplay — "opens a window", "piece falls",
"rotation works" — that neither a diff nor a headless run can establish. Two
equivalent implementations, with identical objective evidence, scored **10 and 2**.

The judge was not the defect. The rubric asked it to see things that were not in its
evidence, and the case gave the lab nothing else to grade with.

## Decision

**A graded case ships its own tests, and each test is one validation command.**
`benchmarks/tetris-v2/` states a `Game` contract in `prompt_default.md` and carries
`tests/test_game.py` — ten headless assertions (spawn, gravity, walls, rotation, lock,
stacking, line clear, scoring, game over, determinism). `task.yaml` lists one
`pytest ...::test_name` command per test, so `test_pass_rate` has the resolution of the
suite without any parser work. The first command is a tamper guard
(`git diff --quiet HEAD -- benchmarks/tetris-v2/tests`) and `files_forbidden` names the
test file, so an agent that edits the grade fails it.

**Rubric levels say what can establish them.** Every level in a `scoring.md` carries
`[check]`, `[diff]` or `[manual]`. The judge's system prompt awards a `[check]` level
only on a PASS, a `[diff]` level only with a quoted citation (returned in a new
`evidence` field), and a `[manual]` level never. `tetris-v1`'s rubric is retagged and
honestly caps the automated judge at level 2 — which is the truth about that case and
the reason `tetris-v2` exists.

**The judge's level is a score row.** `rubric_score` enters the registry as
JUDGEMENT provenance, emitted only when `score_source == "llm_judge"`. It rides in the
deltas table where a 10-vs-2 split is finally visible, and never in the success rate.

**The runner image carries pytest.** Validation runs the shipped tests from `/app`
inside the runner container, which installed only the app's runtime dependencies.

## Consequences

`test_pass_rate` leads the experiment highlights, and `no_headroom` is far less likely
on a ten-assertion case than on a one-command one. The agent is told the tests exist
and where they are: both arms discover the same contract, which is what keeps the
comparison about the harness.

The contract constrains the design — a `Game` class with a fixed API — the way
`cli-textstats` constrains stdout. That is the price of an objective grade, and it is
paid once per case rather than on every run by a judge guessing.

The cost of one validation pass rises from one container start to twelve. At roughly
two seconds each it is noise beside a run.

`tetris-v1` is retired: its benchmark directory, its manual-scoring lane
(`score_tetris.py`, `show_results.py`, `summarize_results.py`, `bench-results`) and
its startup seed are gone, and the app now seeds every `benchmarks/*/task.yaml`
instead. A case whose automated grade caps at "runs without crashing" cannot carry a
harness experiment, and a corpus should not offer what it cannot measure. Its archived
runs under `data/runs/tetris-v1-*` remain as history.
