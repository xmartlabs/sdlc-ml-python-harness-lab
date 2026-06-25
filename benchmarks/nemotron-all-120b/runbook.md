# Runbook: Nemotron All 120B

## Scope

Planner and executor both use `nemotron-super-120b`.

## 1. Choose run ID

Use `tetris-v1-I-00X`.

Example:

```bash
uv run python scripts/create_run.py --benchmark tetris-v1 --run tetris-v1-I-001
```

## 2. Record pre-run estimates

Before running:

- `estimated_input_tokens`
- `estimated_output_tokens`
- `estimated_cost_usd`

## 3. Configure model wiring

```bash
OPENCODE_MODEL_PLAN=gateway/nemotron-120b-planner
OPENCODE_MODEL=gateway/nemotron-120b-executor
GATEWAY_BASE_URL=http://localhost:4000/v1
GATEWAY_TOKEN=<virtual key from create_run>
```

## 4. Generate backlog with planner prompt

Use `prompts/planner.md` to force small, verifiable steps.

## 5. Execute one step at a time

Use `prompts/executor.md`.

Rules:

- one step per turn,
- only allowed files,
- validate before handoff,
- stop on blocker.

## 6. Review each step

Use `prompts/reviewer.md` to detect scope creep and right-size issues.

## 7. Close run

```bash
uv run python scripts/bench_close.py --run tetris-v1-I-001 --score <0-10> --interventions <count>
```

## 8. Fill actual metrics and recommendation

Populate:

- actual tokens/cost/time/turns
- functional, engineering, reproducibility scores
- verdict and recommendation

Recommendation must state whether all-Nemotron should be:

- promoted for specific case types,
- kept as reference only,
- rejected for cost/latency reasons.
