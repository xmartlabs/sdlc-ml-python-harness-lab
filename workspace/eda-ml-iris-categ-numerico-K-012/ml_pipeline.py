"""ML pipeline for Iris-like flower classification with categorical variables."""

import json
import pickle
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split, StratifiedKFold
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score


def make_dataset(random_state: int = 42):
    """Generate an Iris-like dataset with numeric and categorical features.

    Returns:
        X_df: DataFrame with numeric and categorical columns
        y: Series with 3 classes (0, 1, 2)
    """
    np.random.seed(random_state)

    n_samples = 150
    samples_per_class = n_samples // 3

    sepal_length = np.concatenate([
        np.random.normal(5.0, 0.4, samples_per_class),
        np.random.normal(6.0, 0.4, samples_per_class),
        np.random.normal(6.8, 0.4, samples_per_class)
    ])

    sepal_width = np.concatenate([
        np.random.normal(3.4, 0.4, samples_per_class),
        np.random.normal(2.8, 0.4, samples_per_class),
        np.random.normal(3.0, 0.4, samples_per_class)
    ])

    petal_length = np.concatenate([
        np.random.normal(1.5, 0.3, samples_per_class),
        np.random.normal(4.2, 0.5, samples_per_class),
        np.random.normal(5.8, 0.4, samples_per_class)
    ])

    petal_width = np.concatenate([
        np.random.normal(0.3, 0.2, samples_per_class),
        np.random.normal(1.3, 0.2, samples_per_class),
        np.random.normal(2.0, 0.3, samples_per_class)
    ])

    sepal_length = np.clip(sepal_length, 4.0, 8.0)
    sepal_width = np.clip(sepal_width, 2.0, 4.5)
    petal_length = np.clip(petal_length, 1.0, 7.0)
    petal_width = np.clip(petal_width, 0.1, 2.5)

    petal_length_quartiles = pd.qcut(petal_length, q=3, labels=['small', 'medium', 'large'], duplicates='drop')

    X_df = pd.DataFrame({
        'sepal_length': sepal_length,
        'sepal_width': sepal_width,
        'petal_length': petal_length,
        'petal_width': petal_width,
        'petal_size_category': petal_length_quartiles
    })

    y = np.repeat([0, 1, 2], samples_per_class)
    y = pd.Series(y, name='target')

    return X_df, y


def eda_summary(X_df, y):
    """Generate EDA summary statistics.

    Returns:
        dict with keys: missing_values, class_distribution, categorical_frequencies, numeric_summary
    """
    numeric_cols = X_df.select_dtypes(include=[np.number]).columns.tolist()
    categorical_cols = X_df.select_dtypes(include=['object', 'category']).columns.tolist()

    missing_values = X_df.isnull().sum().to_dict()

    class_distribution = y.value_counts().sort_index().to_dict()

    categorical_frequencies = {}
    for col in categorical_cols:
        categorical_frequencies[col] = X_df[col].value_counts().to_dict()

    numeric_summary = {}
    for col in numeric_cols:
        numeric_summary[col] = {
            'min': float(X_df[col].min()),
            'max': float(X_df[col].max()),
            'mean': float(X_df[col].mean()),
            'std': float(X_df[col].std())
        }

    return {
        'missing_values': missing_values,
        'class_distribution': class_distribution,
        'categorical_frequencies': categorical_frequencies,
        'numeric_summary': numeric_summary
    }


def train_and_evaluate(X_df, y, random_state: int = 42):
    """Train and evaluate a classification model.

    Returns:
        dict with keys: accuracy, f1_macro, model (fitted pipeline)
    """
    numeric_cols = X_df.select_dtypes(include=[np.number]).columns.tolist()
    categorical_cols = X_df.select_dtypes(include=['object', 'category']).columns.tolist()

    X_train, X_test, y_train, y_test = train_test_split(
        X_df, y, test_size=0.2, random_state=random_state, stratify=y
    )

    preprocessor = ColumnTransformer(
        transformers=[
            ('num', StandardScaler(), numeric_cols),
            ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), categorical_cols)
        ],
        remainder='drop'
    )

    pipeline = Pipeline([
        ('preprocessor', preprocessor),
        ('classifier', RandomForestClassifier(n_estimators=100, random_state=random_state))
    ])

    pipeline.fit(X_train, y_train)

    y_pred = pipeline.predict(X_test)

    accuracy = float(accuracy_score(y_test, y_pred))
    f1_macro = float(f1_score(y_test, y_pred, average='macro', zero_division=0))

    return {
        'accuracy': accuracy,
        'f1_macro': f1_macro,
        'model': pipeline
    }
