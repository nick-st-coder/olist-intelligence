"""Tests for the model pipelines, the preprocessing contract and the leakage guard."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.config import DATA_DIR, FORBIDDEN_FEATURES, RAW_FILES
from src.features.build_features import ALL_FEATURES, BOOLEAN_FEATURES, CATEGORICAL_FEATURES
from src.modeling.models import build_model, model_names
from src.modeling.preprocessing import build_preprocessor
from src.modeling.splitting import load_model_config


@pytest.fixture(scope="module")
def config() -> dict:
    return load_model_config()


@pytest.fixture(scope="module")
def toy_frame() -> pd.DataFrame:
    """A small frame with the real feature schema, including missing values and unseen levels."""
    rng = np.random.default_rng(0)
    n = 400
    frame = pd.DataFrame(
        {c: rng.normal(10, 3, n) for c in ALL_FEATURES if c not in CATEGORICAL_FEATURES}
    )
    frame["customer_state"] = rng.choice(["SP", "RJ", "MG"], n)
    frame["seller_state"] = rng.choice(["SP", "PR"], n)
    frame["product_category"] = rng.choice(["toys", "books", "garden"], n)
    frame["is_late"] = (rng.random(n) < 0.15).astype(int)

    # Missing values in the historical block, exactly as a new seller produces them.
    frame.loc[frame.index[:40], "seller_prior_late_rate"] = np.nan
    frame.loc[frame.index[:40], "seller_prior_mean_delivery_days"] = np.nan
    return frame


def test_every_declared_feature_is_used_by_the_preprocessor() -> None:
    preprocessor = build_preprocessor(scale_numeric=False)
    used = {c for _name, _t, cols in preprocessor.transformers for c in cols}
    assert used == set(ALL_FEATURES)


def test_no_declared_feature_is_forbidden() -> None:
    assert not (set(ALL_FEATURES) & FORBIDDEN_FEATURES)


def test_preprocessor_handles_missing_values(toy_frame: pd.DataFrame) -> None:
    preprocessor = build_preprocessor(scale_numeric=False)
    matrix = preprocessor.fit_transform(toy_frame[list(ALL_FEATURES)])
    assert not np.isnan(np.asarray(matrix, dtype=float)).any()


def test_preprocessor_ignores_unseen_categories(toy_frame: pd.DataFrame) -> None:
    preprocessor = build_preprocessor(scale_numeric=False)
    preprocessor.fit(toy_frame[list(ALL_FEATURES)])

    unseen = toy_frame[list(ALL_FEATURES)].head(3).copy()
    unseen["customer_state"] = "ZZ"
    matrix = preprocessor.transform(unseen)
    assert matrix.shape[0] == 3


def test_preprocessor_output_is_numeric_and_finite(toy_frame: pd.DataFrame) -> None:
    preprocessor = build_preprocessor(scale_numeric=True)
    matrix = np.asarray(preprocessor.fit_transform(toy_frame[list(ALL_FEATURES)]), dtype=float)
    assert np.isfinite(matrix).all()


def test_scaling_changes_the_numeric_block(toy_frame: pd.DataFrame) -> None:
    scaled = build_preprocessor(scale_numeric=True).fit_transform(toy_frame[list(ALL_FEATURES)])
    raw = build_preprocessor(scale_numeric=False).fit_transform(toy_frame[list(ALL_FEATURES)])
    assert not np.allclose(np.asarray(scaled, dtype=float), np.asarray(raw, dtype=float))


@pytest.mark.parametrize(
    "name", ["dummy", "logistic_regression", "random_forest", "lightgbm", "xgboost"]
)
def test_every_model_fits_and_predicts_probabilities(
    name: str, config: dict, toy_frame: pd.DataFrame
) -> None:
    pipeline = build_model(name, config, calibrate=False)
    pipeline.fit(toy_frame[list(ALL_FEATURES)], toy_frame["is_late"])

    proba = pipeline.predict_proba(toy_frame[list(ALL_FEATURES)])[:, 1]
    assert len(proba) == len(toy_frame)
    assert ((proba >= 0) & (proba <= 1)).all()


def test_calibration_returns_probabilities_on_the_observed_scale(
    config: dict, toy_frame: pd.DataFrame
) -> None:
    pipeline = build_model("logistic_regression", config, calibrate=True)
    pipeline.fit(toy_frame[list(ALL_FEATURES)], toy_frame["is_late"])
    proba = pipeline.predict_proba(toy_frame[list(ALL_FEATURES)])[:, 1]
    assert proba.mean() == pytest.approx(toy_frame["is_late"].mean(), abs=0.12)


def test_unknown_model_name_raises(config: dict) -> None:
    with pytest.raises(ValueError, match="unknown model"):
        build_model("gradient_boosting_unicorn", config)


def test_model_names_are_ordered_from_baseline_to_ensemble(config: dict) -> None:
    names = model_names(config)
    assert names[0] == "dummy"
    assert names.index("logistic_regression") < names.index("lightgbm")


def test_feature_subset_restricts_the_pipeline(config: dict, toy_frame: pd.DataFrame) -> None:
    subset = ["n_items", "estimated_lead_days", "customer_state"]
    pipeline = build_model("logistic_regression", config, features=subset, calibrate=False)
    pipeline.fit(toy_frame[subset], toy_frame["is_late"])
    assert pipeline.predict_proba(toy_frame[subset]).shape[0] == len(toy_frame)


def test_boolean_features_are_numeric_in_the_feature_frame() -> None:
    """``SimpleImputer`` rejects ``bool`` dtype, so the cast must happen before the pipeline."""
    if not all((DATA_DIR / path).is_file() for path in RAW_FILES.values()):
        pytest.skip("raw CSVs absent; download them from Kaggle into data/")
    from src.features.build_features import build_feature_frame
    from src.ingestion.extract import load_raw_tables
    from src.preprocessing.cleaning import build_order_level_dataset

    order_level = build_order_level_dataset(load_raw_tables())
    features = build_feature_frame(order_level)
    for column in BOOLEAN_FEATURES:
        assert features[column].dtype == np.float64
