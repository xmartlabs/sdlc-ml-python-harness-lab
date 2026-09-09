# Legacy cli-textstats prompts (not offered as variants)

These two prompts were the original `cli-textstats-v1` prompt family. They are
kept for the record — historical runs reference them by variant name — but they
are no longer declared in `task.yaml`, so the evaluation wizard does not offer
them.

They were removed because they vary the **task**, not the methodology:

| file | what it adds beyond the task | states the graded I/O contract? |
|---|---|---|
| `prompt_naive.md` | nothing | partly — no unknown-subcommand rule, no exact assertions |
| `prompt_sdlc.md` | a plan/implement/test/verify loop **and** a `tests/` deliverable | yes |

`prompt_sdlc.md` asks for a test suite that `prompt_naive.md` never mentions, so
pairing them measures a difference in the assignment on top of any difference in
the harness. It also spells the contract out more precisely than the naive side
does, which hands the treatment arm an advantage that has nothing to do with the
harness.

`task.yaml` now declares exactly two variants, `default` and `harness`, whose
task bodies are byte-identical (see ADR-0047). The contract that
`validation_commands` grades — including the exact assertions — lives in
`prompt_default.md`, so both arms are issued the requirement they are scored on.

Historical runs are unaffected: every run archives the prompt it was actually
given at `data/runs/<run_id>/prompt.md`.
