# Nemotron Planner + Qwen Executor

## Experiment ID

`nemotron-planner-qwen-executor`

## Hypothesis

A stronger planner (`nemotron-super-120b`) can produce more executable backlogs for local executors,
reducing loops, oversized steps, and failed validation while keeping total cost below a full
frontier-only workflow.

## Objective

Evaluate planner/executor split quality in a reproducible benchmark setup:

- Planner: `nemotron-super-120b`
- Executors: `qwen3.6-35b` and `qwen3.5-9b` (Qwen 8B class)

## When to use this experiment

Use this experiment when you need to decide whether:

- a large planner is enough to improve small/medium executors,
- local executors are viable for engineering tasks with strict step discipline,
- all-cloud frontier models are necessary for full completion.

## Related benchmark matrix

This experiment maps to `tetris-v1` cells:

- `F`: Nemotron planner + Qwen 35B executor
- `G`: Nemotron planner + Qwen 3.5-9B executor
- `H`: Qwen 35B planner + Qwen 35B executor (local homogeneous baseline)
- `I`: Nemotron planner + Nemotron executor (all-Nemotron reference)
- `E`: GPT-5.5 cloud baseline (optional reference)

## Methodology

1. Select a reproducible case (`tetris-v1` for first pass).
2. Freeze base commit and prompt variants.
3. Record pre-run cost/token estimates.
4. Run planner once to produce a small executable backlog.
5. Execute one step at a time with the selected executor.
6. Validate after each step.
7. Capture traces in Langfuse with canonical metadata.
8. Score functional, engineering, reproducibility, cost, and intervention metrics.
9. Produce recommendation for harness/template/prompt/model updates.

## Expected outputs

- `runs.yaml` rows with planned and actual metrics
- run-level report in `data/runs/<run-id>/report.md`
- Langfuse session-level usage export
- recommendation for reusable Lab assets

## Success criteria

- `functional_score >= 7`
- `engineering_score >= 3`
- `reproducibility_score >= 3`
- `human_interventions <= 2`
- cost variance between estimate and actual stays controlled (`cost_score` target <= 1.2)

## Failure criteria

- repeated scope creep despite reviewer checks
- large-step plans that break runnability requirements
- persistent validation failures or blocked execution
- high human intervention dependency

## Connection to AI Adoption Lab next steps

This experiment directly supports the Lab mission:

- collect real or semi-real cases with structured evidence,
- estimate and track cost before/after execution,
- compare open-source local executors vs larger models,
- test whether large planners can unlock local execution,
- generate reusable recommendations without forcing a single harness.
