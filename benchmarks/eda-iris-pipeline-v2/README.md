# eda-iris-pipeline-v2

An end-to-end tabular ML task — dataset with a derived categorical feature, EDA summary,
scikit-learn pipeline, demo writing `eda_report.json` and `model.joblib` — graded by the
lab's own tests in `tests/test_iris_pipeline.py`.

## Why a v2

`eda-ml-iris-categ-numerico` asked the agent to write `test_pipeline_iris_eda.py` and
then graded the run on that same file. A run that wrote six easy tests passed; one that
wrote twenty-five demanding ones and failed one did not. On 2026-10-23 its twelve runs
also failed `pytest` at collection, because the offline validation container had no
pandas. Here the tests are the lab's, and the case declares `validation_requirements`
the validation setup installs before the offline checks (ADR-0071).

## Calibration (2026-10-24)

| Workspace | Graded tests passed |
|---|---|
| Reference solution | 12 / 12 |
| Empty | 0 / 12 |
| Encoder without `handle_unknown` | 11 / 12 |
| No `main.py`, encoder without `handle_unknown` | 10 / 12 |
| The six claude-opus-5-5 runs of `do-stronger-models-need-less-harness-2x3` | 12 / 12 each |
| The four claude-haiku-4-5 runs that produced code | 10–11 / 12 |

The reference solution is not in the repository: an agent that could read it would be
graded on copying.
