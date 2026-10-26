# eda-iris-pipeline-v2 scoring

The grade is the shipped suite: `test_pass_rate` over 12 tests, all objective
`[check]`. A strict pass is all 12. The tests are the lab's: the agent writes its own as
part of the work, and they are not graded — its predecessor,
`eda-ml-iris-categ-numerico`, graded each run on the tests the run wrote (ADR-0071).

| Level | Meaning | Evidence |
|-------|---------|----------|
| 0 | `ml_pipeline.py` missing or does not import | [check] |
| 5 | About half of the tests pass — data and EDA, not the pipeline or the demo | [check] |
| 10 | Every test passes, including the saved model predicting an unseen category | [check] |
