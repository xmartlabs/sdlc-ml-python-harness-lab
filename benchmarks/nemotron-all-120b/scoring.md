# Scoring: Nemotron All 120B

## Metrics

- `functional_score` (0-10)
- `engineering_score` (0-5)
- `reproducibility_score` (0-5)
- `cost_score = actual_cost_usd / estimated_cost_usd`
- `human_intervention_count`

## Pass/fail

PASS when:

- `functional_score >= 8`
- `engineering_score >= 3`
- `reproducibility_score >= 3`
- `human_intervention_count <= 2`

Otherwise FAIL.

## Notes

- Reuse the same functional ladder used in `benchmarks/tetris/scoring.md`.
- Keep scoring method consistent with other benchmark cells for comparability.
