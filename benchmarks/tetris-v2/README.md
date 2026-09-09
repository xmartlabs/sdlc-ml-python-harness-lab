# tetris-v2

`tetris-v1` with the grade moved from an operator playing the game (and an LLM
judge guessing from a diff) to a shipped test suite. The agent gets the `Game`
contract in the prompt and the tests at `/app/benchmarks/tetris-v2/tests`; each
test is one validation command, so `test_pass_rate` has the resolution of the
suite (ADR-0050).

The first validation command is a tamper guard: the tests must be byte-identical
to the checked-in ones. `files_forbidden` says the same thing to the scorer.

```bash
make prompts                                 # regenerate prompt_harness.md
make case-import TASK=benchmarks/tetris-v2/task.yaml
```

The runner image ships `pytest` for this case (`Dockerfile.runner`); rebuild it
(`make app-up`) after pulling.
