# cli-textstats-v1 — objective agentic build case

A small, self-contained `build_from_scratch` case for comparing harnesses and
models on a realistic 2026 agentic coding task, **without any external dataset**.
The agent builds a single-file stdin text-stats CLI (`main.py`); a fixed set of
input/output assertions scores the functional floor objectively.

- **Task spec:** [task.yaml](task.yaml)
- **Prompt variants:** `naive` ([prompt_naive.md](prompt_naive.md)) and `sdlc`
  ([prompt_sdlc.md](prompt_sdlc.md)) — use them to A/B the harness (does an SDLC
  prompt + skills beat a one-line ask?).
- **Objective validation:** three exit-code assertions on `words` / `lines` /
  `unique` (see `validation_commands` in task.yaml). Not gameable by a
  self-check the agent writes.

## Use it

```bash
make case-import TASK=benchmarks/cli-textstats/task.yaml   # one-time, idempotent
```

Then create a run against `cli-textstats-v1` from the web UI (or `make
bench-run`), pick a prompt variant and model, and compare outcomes. Like
tetris-v1 the final score is manual, but the objective checks populate the
validation panel automatically, so "does it actually work" is scored for you.

## Extend it

Copy this folder as a template for new agentic cases. Keep the I/O contract in
the prompt exact, and make every `validation_commands` entry a shell assertion
that exits non-zero on any mismatch — that is what turns a prompt into an
objectively-scored benchmark.
