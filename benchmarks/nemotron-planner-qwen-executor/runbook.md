# Runbook: Nemotron Planner + Qwen Executor

## Scope

This runbook executes planner/executor runs with:

- planner: `nemotron-super-120b`
- executor: `qwen3.6-35b` or `qwen3.5-9b`

## 1. Choose case and run ID

Use `tetris-v1` first for direct comparability.

Examples:

- `tetris-v1-F-001` (Nemotron + Qwen 35B)
- `tetris-v1-G-001` (Nemotron + Qwen 3.5-9B)

## 2. Register run and scaffold artifacts

```bash
uv run python scripts/create_run.py --benchmark tetris-v1 --run tetris-v1-F-001
```

This updates status to `running`, scaffolds `data/runs/<run-id>/`, and can create a virtual key.

## 3. Estimate cost and tokens before execution

Record in run metadata/report template:

- `estimated_input_tokens`
- `estimated_output_tokens`
- `estimated_cost_usd`

Use prior run medians as baseline (same prompt variant and model pair).

## 4. Configure planner/executor models

In the subject worktree `.env`:

```bash
OPENCODE_MODEL_PLAN=gateway/nemotron-120b-planner
OPENCODE_MODEL=gateway/lmstudio-qwen3.6-35b-build   # for F
# OPENCODE_MODEL=gateway/qwen9b-executor             # for G
GATEWAY_BASE_URL=http://localhost:4000/v1
GATEWAY_TOKEN=<virtual key from create_run>
```

## 5. Run planner once, produce executable backlog

Use `prompts/planner.md`. Planner must output small steps, each with:

- allowed files
- validation command
- acceptance criteria
- strict scope boundary

## 6. Execute one step at a time

Use `prompts/executor.md` for each step.

Rules:

- execute one step only
- edit only allowed files
- run validation
- stop and report blocker if validation cannot pass without scope expansion

## 7. Review every completed step

Use `prompts/reviewer.md`.

Reviewer checks:

- acceptance criteria fulfilled
- no scope creep
- step size remains executor-safe

## 8. Close run and capture result

```bash
uv run python scripts/bench_close.py --run tetris-v1-F-001 --score <0-10> --interventions <count>
```

## 9. Populate actual metrics

Update run/report with:

- `actual_input_tokens`
- `actual_output_tokens`
- `actual_cost_usd`
- `total_wall_time_minutes`
- `turns`
- `functional_score`
- `engineering_score`
- `reproducibility_score`
- `verdict`
- `recommendation`

## 10. Produce recommendation for Lab assets

Decide what should be:

- adopted into harness/template/prompt defaults,
- tested in additional benchmarks,
- rejected due to cost or instability.
