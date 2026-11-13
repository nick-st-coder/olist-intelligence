"""Tests for per-order SHAP explanations.

A contribution table full of zeros is not a weak explanation, it is a broken one. The explainer
must be built from a reference distribution of *other* rows: passing the row under inspection as
its own baseline makes every Shapley value exactly zero, because nothing deviates from itself.
That failure is silent — a table of zeros looks like a valid, if uninformative, answer — so it is
asserted against directly, together with the additivity identity that makes the numbers mean
something.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.config import ORDER_LEVEL_PATH
from src.features.build_features import ALL_FEATURES
from src.features.historical import build_completion_timeline
from src.modeling.explain import decision_explanation, explain_row, source_feature
from src.modeling.predict import DEFAULT_MODEL_PATH, load_pipeline
from src.serving.features import build_serving_features


@pytest.fixture(scope="module")
def orders() -> pd.DataFrame:
    if not ORDER_LEVEL_PATH.exists():
        pytest.skip("analytical dataset absent")
    return pd.read_parquet(ORDER_LEVEL_PATH)


@pytest.fixture(scope="module")
def completion(orders: pd.DataFrame) -> pd.DataFrame:
    return build_completion_timeline(orders)


@pytest.fixture(scope="module")
def pipeline():
    if not DEFAULT_MODEL_PATH.exists():
        pytest.skip("no trained artefact")
    return load_pipeline()


@pytest.fixture(scope="module")
def features(orders: pd.DataFrame, completion: pd.DataFrame) -> pd.DataFrame:
    """A hundred real orders, assembled exactly as the service assembles them."""
    sample = orders[orders["is_late"].notna()].iloc[1000:1100]
    return build_serving_features(sample, completion=completion)


def test_source_feature_collapses_a_one_hot_level() -> None:
    assert source_feature("customer_state_SP") == "customer_state"
    assert source_feature("categorical__product_category_toys") == "product_category"
    assert source_feature("numeric__estimated_lead_days") == "estimated_lead_days"


def test_explain_row_returns_non_zero_contributions(pipeline, features) -> None:
    """Regression: explaining a row against itself yields all zeros."""
    rows = explain_row(pipeline, features, features.index[0], top_k=10)

    assert not rows.empty
    assert (rows["shap_value"] != 0).any(), "every contribution was zero"


def test_explain_row_contributions_are_additive(pipeline, features) -> None:
    """Contributions plus the base value must reconstruct the explained score."""
    from src.modeling.explain import _background_matrix, _explainer, _transformed, _unwrap

    row_index = features.index[0]
    matrix = _transformed(pipeline, features.loc[[row_index]])
    baseline = _background_matrix(pipeline, features, row_index, None, 100, 42)
    explainer = _explainer(pipeline, baseline, max_samples=len(baseline))

    contributions = np.asarray(explainer.shap_values(matrix)).ravel()
    base = float(np.ravel(explainer.expected_value)[0])
    # Calibration is a monotone wrapper, so the score it explains is the inner estimator's.
    decision = float(_unwrap(pipeline.named_steps["model"]).decision_function(matrix.values)[0])

    assert contributions.sum() + base == pytest.approx(decision, abs=1e-6)


def test_decision_explanation_groups_by_business_feature(pipeline, features) -> None:
    explanation = decision_explanation(
        pipeline, features, features.index[0], threshold=0.15, top_k=5
    )

    reported = [row["feature"] for row in explanation["increases_risk"]]
    reported += [row["feature"] for row in explanation["decreases_risk"]]
    assert reported
    # Grouped names are source features, never one-hot levels.
    for name in reported:
        assert name in ALL_FEATURES, f"{name} is a transformed column, not a business feature"
    assert 0.0 <= explanation["probability"] <= 1.0


def test_explain_row_requires_a_reference_distribution(pipeline) -> None:
    """A single-row frame leaves no reference to compare against, so it must fail loudly."""
    single = pd.DataFrame(
        [
            {
                "order_purchase_timestamp": "2018-02-01T10:00:00",
                "order_estimated_delivery_date": "2018-03-01",
            }
        ]
    )
    features = build_serving_features(single)
    with pytest.raises(ValueError, match="reference distribution"):
        explain_row(pipeline, features, features.index[0])
