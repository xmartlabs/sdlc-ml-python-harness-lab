# Task: an end-to-end EDA and classification pipeline on Iris-style data

Build a small, self-contained Python project in `{workspace}`: an Iris-style dataset
with numeric and categorical features, a short EDA, a scikit-learn pipeline, and a demo
that writes its artifacts. Use pandas, numpy, scikit-learn and joblib. Do not download
anything at run time — build the dataset in code (`sklearn.datasets.load_iris` ships
with scikit-learn and is fine).

## The contract (exact public names)

`{workspace}/ml_pipeline.py`:

```python
make_dataset(random_state: int) -> tuple[pandas.DataFrame, Sequence]
eda_summary(X_df, y) -> dict
train_and_evaluate(X_df, y, random_state: int) -> dict
```

- `make_dataset` returns `(X_df, y)`: at least 4 numeric columns plus at least one
  **categorical column derived from the data** (for example quantile bins labelled
  `small` / `medium` / `large`), and `y` with 3 classes. The same `random_state` gives
  the same data.
- `eda_summary` returns a JSON-serializable dict with exactly these sections:
  `numeric_summary` (min, max and mean per numeric column), `categorical_frequencies`
  (value counts per categorical column), `missing_values` (count per column) and
  `class_distribution` (count per class).
- `train_and_evaluate` splits train/test (stratified), preprocesses numeric columns
  (for example a scaler) and categorical ones (one-hot), trains a classifier and returns
  `{"accuracy": float, "f1_macro": float}`, both in `[0, 1]`.
- The pipeline must not break on a category it never saw in training.

`{workspace}/main.py` runs the pipeline as a demo and writes, next to itself:

- `eda_report.json` — the `eda_summary` result;
- `model.joblib` — the trained pipeline (preprocessing and classifier together), so
  `joblib.load(...).predict(X_df)` works on raw rows.

Write your own pytest tests in `{workspace}` as you go, and a minimal
`requirements.txt`.

## Acceptance: the graded tests

The graded tests live at `/app/benchmarks/eda-iris-pipeline-v2/tests/test_iris_pipeline.py`
and import `ml_pipeline` from the workspace. Read them before you start — they are the
acceptance criteria. Run them with:

```bash
cd {workspace} && IRIS_WORKSPACE="$PWD" python -m pytest -c /dev/null -p no:cacheprovider -q /app/benchmarks/eda-iris-pipeline-v2/tests/test_iris_pipeline.py
```

Do not edit the graded tests.
