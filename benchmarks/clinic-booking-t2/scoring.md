# clinic-booking-t2 scoring

The grade is the shipped suite: `test_pass_rate` over 14 tests, all objective
`[check]`. A strict pass is all 14. This case is tier 2 of the clinic
ladder; a model-harness pair *reliably* does the tier when every case of the tier passes
strictly in at least 4 of 5 runs and the pooled 95% lower bound is at least 0.70
(ADR-0069).

| Level | Meaning | Evidence |
|-------|---------|----------|
| 0 | `clinic.py` missing or does not import | [check] |
| 5 | About half of the tests pass — the happy path, not the rules | [check] |
| 10 | Every test passes | [check] |
