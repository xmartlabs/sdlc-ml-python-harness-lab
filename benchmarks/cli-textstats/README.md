# cli-textstats-v1 — objective agentic build case

A small, self-contained `build_from_scratch` case for comparing harnesses and
models on a realistic 2026 agentic coding task, **without any external dataset**.
The agent builds a single-file stdin text-stats CLI (`main.py`); a fixed set of
input/output assertions scores the functional floor objectively.

- **Task spec:** [task.yaml](task.yaml)
- **Prompt variants:** exactly two (ADR-0047) — `default`
  ([prompt_default.md](prompt_default.md)) is the task; `harness`
  ([prompt_harness.md](prompt_harness.md)) is the same task body with
  [`_harness_preamble.md`](../_harness_preamble.md) prepended. Edit the default
  and run `make prompts`; `make prompts-check` fails if they drift. The retired
  `naive`/`sdlc` pair is in [legacy-prompts/](legacy-prompts/).
- **Objective validation:** three exit-code assertions on `words` / `lines` /
  `unique` (see `validation_commands` in task.yaml). Not gameable by a
  self-check the agent writes.

## Use it

```bash
make case-import TASK=benchmarks/cli-textstats/task.yaml   # one-time, idempotent
```

Already imported and the prompts changed? Re-run it with `--refresh` to
re-read `prompts` and `validation_commands` from the YAML:

```bash
uv run python scripts/import_case.py --task benchmarks/cli-textstats/task.yaml --refresh
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
