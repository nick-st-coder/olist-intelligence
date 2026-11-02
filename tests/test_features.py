"""Tests for the feature builder and the leakage contract."""

from __future__ import annotations

import pandas as pd
import pytest
from src.config import FORBIDDEN_FEATURES
from src.features.build_features import (
    BOOLEAN_FEATURES,
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    assert_no_leakage,
    build_feature_frame,
    build_target,
)
from src.ingestion.extract import load_raw_tables
from src.preprocessing.cleaning import build_order_level_dataset


@pytest.fixture(scope="module")
def order_level() -> pd.DataFrame:
    return build_order_level_dataset(load_raw_tables())


def test_feature_frame_contains_no_forbidden_column(order_level: pd.DataFrame) -> None:
    features = build_feature_frame(order_level)
    assert not (FORBIDDEN_FEATURES & set(features.columns))


def test_feature_frame_has_one_row_per_labelled_order(order_level: pd.DataFrame) -> None:
    features = build_feature_frame(order_level)
    assert len(features) == int(order_level["is_late"].notna().sum())


def test_target_aligns_with_feature_frame(order_level: pd.DataFrame) -> None:
    features = build_feature_frame(order_level)
    target = build_target(order_level)
    assert len(features) == len(target)
    assert set(target.unique()) <= {0, 1}
    assert target.mean() == pytest.approx(0.0811, abs=0.001)


def test_assert_no_leakage_raises_on_a_forbidden_column() -> None:
    with pytest.raises(ValueError, match="leakage detected"):
        assert_no_leakage(["n_items", "review_score"])


def test_assert_no_leakage_passes_on_clean_columns() -> None:
    assert_no_leakage(["n_items", "estimated_lead_days", "customer_state"])


def test_every_declared_feature_is_present(order_level: pd.DataFrame) -> None:
    features = build_feature_frame(order_level)
    declared = {*NUMERIC_FEATURES, *BOOLEAN_FEATURES, *CATEGORICAL_FEATURES}
    assert declared <= set(features.columns)


def test_no_declared_feature_is_forbidden() -> None:
    declared = {*NUMERIC_FEATURES, *BOOLEAN_FEATURES, *CATEGORICAL_FEATURES}
    assert not (declared & FORBIDDEN_FEATURES)
