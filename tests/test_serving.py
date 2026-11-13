"""Tests for the serving feature builder.

The property that matters here is that a scored order is assembled from exactly the same inputs,
and with exactly the same derivations, as a trained order. Any divergence means the model is
scoring on a distribution it never fitted — a failure that raises no error and shows up only as
quietly worse predictions.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.config import FORBIDDEN_FEATURES, ORDER_LEVEL_PATH
from src.features.build_features import ALL_FEATURES, build_feature_frame
from src.features.historical import build_completion_timeline
from src.serving.features import build_serving_features


@pytest.fixture(scope="module")
def orders() -> pd.DataFrame:
    if not ORDER_LEVEL_PATH.exists():
        pytest.skip("analytical dataset absent")
    return pd.read_parquet(ORDER_LEVEL_PATH)


@pytest.fixture(scope="module")
def completion(orders: pd.DataFrame) -> pd.DataFrame:
    return build_completion_timeline(orders)


def test_serving_frame_matches_the_training_frame(orders, completion) -> None:
    """The same orders produce the same features down both code paths."""
    sample = orders[orders["is_late"].notna()].iloc[500:600]

    training = build_feature_frame(orders).loc[sample.index]
    serving = build_serving_features(sample, completion=completion)

    assert list(serving.columns) == list(ALL_FEATURES)
    for column in ALL_FEATURES:
        left = pd.to_numeric(training[column], errors="coerce")
        right = pd.to_numeric(serving[column], errors="coerce")
        if left.dtype.kind in "if" and right.dtype.kind in "if":
            pd.testing.assert_series_equal(left, right, check_names=False, atol=1e-9)
        else:
            assert (left.astype(str) == right.astype(str)).all(), column


def test_serving_frame_contains_no_forbidden_column(orders, completion) -> None:
    sample = orders.iloc[100:110]
    serving = build_serving_features(sample, completion=completion)
    assert not (FORBIDDEN_FEATURES & set(serving.columns))


def test_serving_frame_survives_a_minimal_payload() -> None:
    """Only the two dates are mandatory; everything else is imputed."""
    payload = pd.DataFrame(
        [
            {
                "order_purchase_timestamp": "2018-02-01T10:00:00",
                "order_estimated_delivery_date": "2018-03-01",
            }
        ]
    )
    features = build_serving_features(payload)

    assert list(features.columns) == list(ALL_FEATURES)
    # 1 Feb 10:00 to 1 Mar 00:00 is 27 complete days; ``.dt.days`` truncates, as in training.
    assert features.loc[0, "estimated_lead_days"] == 27.0
    assert features.loc[0, "purchase_month"] == 2.0
    assert bool(features.loc[0, "seller_has_history"]) is False


def test_serving_frame_derives_distance_from_coordinates() -> None:
    payload = pd.DataFrame(
        [
            {
                "order_purchase_timestamp": "2018-02-01T10:00:00",
                "order_estimated_delivery_date": "2018-03-01",
                "customer_lat": -23.55,
                "customer_lng": -46.63,
                "seller_lat": -22.90,
                "seller_lng": -43.20,
            }
        ]
    )
    features = build_serving_features(payload)
    # Sao Paulo to Rio de Janeiro is roughly 360 km.
    assert 330.0 < features.loc[0, "seller_customer_distance_km"] < 400.0


def test_serving_frame_flags_a_cross_state_route() -> None:
    payload = pd.DataFrame(
        [
            {
                "order_purchase_timestamp": "2018-02-01T10:00:00",
                "order_estimated_delivery_date": "2018-03-01",
                "customer_state": "RJ",
                "seller_state": "SP",
            }
        ]
    )
    features = build_serving_features(payload)
    assert features.loc[0, "is_cross_state"] == 1.0


def test_serving_frame_ignores_post_purchase_columns(orders, completion) -> None:
    """Passing outcome columns must not change a single feature value."""
    sample = orders[orders["is_late"].notna()].iloc[200:205]
    baseline = build_serving_features(sample, completion=completion)

    tampered = sample.copy()
    tampered["order_delivered_customer_date"] = pd.Timestamp("2030-01-01")
    tampered["order_status"] = "delivered"
    tampered["review_score"] = 1
    after = build_serving_features(tampered, completion=completion)

    pd.testing.assert_frame_equal(baseline, after)
