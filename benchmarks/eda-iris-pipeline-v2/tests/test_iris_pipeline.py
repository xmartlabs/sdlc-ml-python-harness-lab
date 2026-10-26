"""Graded tests for eda-iris-pipeline-v2 (ADR-0071).

The agent writes `ml_pipeline.py` and `main.py` in the workspace; these tests
import them from the directory in IRIS_WORKSPACE (default: the current
directory). They are the lab's, not the agent's: its predecessor graded a run on
the tests the run itself wrote, so six easy tests scored above twenty-five hard
ones.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(os.environ.get("IRIS_WORKSPACE", ".")).resolve()
sys.path.insert(0, str(WORKSPACE))

EDA_KEYS = {"missing_values", "class_distribution", "categorical_frequencies", "numeric_summary"}


@pytest.fixture
def pipeline():
    sys.modules.pop("ml_pipeline", None)
    try:
        return importlib.import_module("ml_pipeline")
    except ImportError as error:  # no ml_pipeline.py yet: a failed test, not a broken suite
        pytest.fail(f"ml_pipeline.py could not be imported from the workspace: {error}")


def _categorical_columns(frame):
    """Every non-numeric, non-boolean column: object, category or pandas'
    string dtype (what `.astype(str)` gives on pandas 2.x and later)."""
    numeric = set(_numeric_columns(frame))
    return [
        column
        for column in frame.columns
        if column not in numeric and str(frame[column].dtype) != "bool"
    ]


def _numeric_columns(frame):
    return list(frame.select_dtypes("number").columns)


def test_the_dataset_has_four_numeric_and_a_categorical_feature(pipeline):
    X, _y = pipeline.make_dataset(random_state=0)
    assert len(_numeric_columns(X)) >= 4
    assert len(_categorical_columns(X)) >= 1


def test_the_dataset_has_three_classes_and_one_label_per_row(pipeline):
    X, y = pipeline.make_dataset(random_state=0)
    labels = list(y)
    assert len(labels) == len(X) >= 30
    assert len(set(labels)) == 3


def test_the_dataset_is_reproducible(pipeline):
    X1, y1 = pipeline.make_dataset(random_state=7)
    X2, y2 = pipeline.make_dataset(random_state=7)
    assert X1.equals(X2)
    assert list(y1) == list(y2)


def test_the_categorical_feature_is_derived_not_constant(pipeline):
    X, _y = pipeline.make_dataset(random_state=0)
    assert any(X[column].nunique() >= 2 for column in _categorical_columns(X))


def test_the_eda_summary_has_every_required_section(pipeline):
    X, y = pipeline.make_dataset(random_state=0)
    summary = pipeline.eda_summary(X, y)
    assert EDA_KEYS <= set(summary)


def test_the_class_distribution_counts_every_row(pipeline):
    X, y = pipeline.make_dataset(random_state=0)
    distribution = pipeline.eda_summary(X, y)["class_distribution"]
    assert len(distribution) == 3
    assert sum(int(count) for count in distribution.values()) == len(X)


def test_missing_values_are_counted_per_column(pipeline):
    X, y = pipeline.make_dataset(random_state=0)
    missing = pipeline.eda_summary(X, y)["missing_values"]
    assert set(map(str, X.columns)) <= set(map(str, missing))
    with_gap = X.copy()
    with_gap.iloc[0, 0] = None
    assert int(pipeline.eda_summary(with_gap, y)["missing_values"][str(X.columns[0])]) == 1


def test_the_eda_summary_is_json_serializable(pipeline):
    X, y = pipeline.make_dataset(random_state=0)
    json.dumps(pipeline.eda_summary(X, y))


def test_training_returns_accuracy_and_macro_f1_in_range(pipeline):
    X, y = pipeline.make_dataset(random_state=0)
    metrics = pipeline.train_and_evaluate(X, y, random_state=0)
    for name in ("accuracy", "f1_macro"):
        assert 0.0 <= float(metrics[name]) <= 1.0


def test_the_classifier_actually_learns(pipeline):
    """Iris separates easily; a pipeline below 0.8 has not learned it."""
    X, y = pipeline.make_dataset(random_state=0)
    assert float(pipeline.train_and_evaluate(X, y, random_state=0)["accuracy"]) >= 0.8


def _run_main():
    """Run the agent's main.py from a clean slate; fail the test if it does not."""
    for name in ("eda_report.json", "model.joblib"):
        (WORKSPACE / name).unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, str(WORKSPACE / "main.py")],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-800:]


def test_main_writes_the_report_and_a_loadable_model():
    _run_main()
    report = json.loads((WORKSPACE / "eda_report.json").read_text())
    assert isinstance(report.get("class_distribution"), dict) and report["class_distribution"]
    import joblib

    model = joblib.load(WORKSPACE / "model.joblib")
    assert hasattr(model, "predict")


def test_the_saved_model_predicts_a_category_it_never_saw(pipeline):
    """The prompt's own requirement — the pipeline must not break on categories it
    did not see. Asked of the saved model directly, so the agent's split cannot
    hide the unseen value inside the training set."""
    _run_main()
    import joblib

    model = joblib.load(WORKSPACE / "model.joblib")
    X, _y = pipeline.make_dataset(random_state=0)
    row = X.head(3).copy()
    column = _categorical_columns(X)[0]
    row[column] = row[column].astype(object)
    row.iloc[:, row.columns.get_loc(column)] = "never_seen"
    assert len(model.predict(row)) == 3
