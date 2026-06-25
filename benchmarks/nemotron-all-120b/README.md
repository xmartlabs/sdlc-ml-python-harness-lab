# Nemotron All 120B

## Experiment ID

`nemotron-all-120b`

## Hypothesis

A full planner+executor setup with `nemotron-super-120b` can complete end-to-end coding tasks in this harness with quality close to frontier cloud baselines.

## Objective

Validate a non-GPT all-large-model path for full task completion while preserving reproducibility and measurable cost.

## When to use this experiment

Use this benchmark when you need to know:

- whether Nemotron alone can complete the full task,
- how all-Nemotron compares to GPT-5.5 cloud baseline,
- whether all-Nemotron is operationally viable versus planner+local-executor variants.

## Related matrix

Primary cell:

- `I`: Nemotron planner + Nemotron executor

Comparison references:

- `E`: GPT-5.5 cloud baseline
- `F`: Nemotron planner + Qwen 35B
- `H`: Qwen 35B planner + Qwen 35B

## Methodology

1. Freeze case and base commit.
2. Record pre-run token/cost estimates.
3. Run planner with strict backlog output contract.
4. Execute one backlog step per turn with Nemotron executor.
5. Validate each step before moving forward.
6. Score and report with the same rubric used in related experiments.

## Expected outputs

- run-level metadata with estimate vs actual usage
- Langfuse trace evidence by role and session
- recommendation on where all-Nemotron is justified or not

## Success criteria

- `functional_score >= 8`
- `engineering_score >= 3`
- `reproducibility_score >= 3`
- `human_interventions <= 2`

## Failure criteria

- frequent loop/retry behavior without progress
- low reproducibility across repetitions
- costs significantly above expectation without quality gain

## Connection to AI Adoption Lab next steps

This experiment strengthens the Lab by providing a direct large-model reference that is not tied
to GPT-only assumptions, enabling better model selection guidance for future harness defaults.
