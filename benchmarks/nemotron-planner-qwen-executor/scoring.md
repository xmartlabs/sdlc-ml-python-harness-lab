# Scoring: Nemotron Planner + Qwen Executor

## Dimensions

### 1) Functional score (0-10)

Use the same functional ladder as `benchmarks/tetris/scoring.md` for comparability.

### 2) Engineering score (0-5)

Assess:

- scope adherence,
- file touch minimization,
- validation discipline,
- diff quality and stability.

### 3) Reproducibility score (0-5)

Assess:

- rerun consistency,
- clarity of runbook and metadata,
- ability to reproduce from frozen prompt + run config.

### 4) Cost score

Use estimate accuracy ratio:

- `cost_score = actual_cost_usd / estimated_cost_usd`
- target: `<= 1.2`

### 5) Human intervention count

Track all manual interventions needed to unblock/redirect execution.

## Pass/fail criteria

PASS when all are true:

- `functional_score >= 7`
- `engineering_score >= 3`
- `reproducibility_score >= 3`
- `human_intervention_count <= 2`

Otherwise: FAIL.

## Required outputs per run

- functional_score
- engineering_score
- reproducibility_score
- cost_score
- human_intervention_count
- verdict
- recommendation
