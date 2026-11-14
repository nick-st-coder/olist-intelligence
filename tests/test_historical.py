"""Tests for the leakage-safe historical seller features."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.features.historical import HISTORY_FEATURES, add_seller_history_features

PURCHASE = "order_purchase_timestamp"
DELIVERED = "order_delivered_customer_date"
SELLER = "primary_seller_id"


def _orders(rows: list[tuple[str, str, str, int | None]]) -> pd.DataFrame:
    """Build a minimal order-level frame from (seller, purchase, delivered, label) tuples."""
    return pd.DataFrame(
        {
            "order_id": [f"o{i}" for i in range(len(rows))],
            SELLER: [r[0] for r in rows],
            PURCHASE: pd.to_datetime([r[1] for r in rows]),
            DELIVERED: pd.to_datetime([r[2] for r in rows]),
            "is_late": pd.array([r[3] for r in rows], dtype="boolean"),
        }
    )


def test_no_history_before_the_first_completed_order() -> None:
    df = _orders([("S1", "2017-01-01", "2017-01-10", 0)])
    out = add_seller_history_features(df)
    assert out.loc[0, "seller_prior_orders"] == 0
    assert pd.isna(out.loc[0, "seller_prior_late_rate"])
    assert not bool(out.loc[0, "seller_has_history"])


def test_history_counts_only_orders_completed_before_the_purchase() -> None:
    # Order A completes on 2017-01-10; B is placed on 2017-02-01 and sees it.
    df = _orders(
        [
            ("S1", "2017-01-01", "2017-01-10", 0),
            ("S1", "2017-02-01", "2017-02-15", 1),
        ]
    )
    out = add_seller_history_features(df)
    assert out.loc[1, "seller_prior_orders"] == 1
    assert out.loc[1, "seller_prior_late_rate"] == 0.0


def test_history_join_accepts_different_datetime_resolutions() -> None:
    df = _orders(
        [
            ("S1", "2017-01-01", "2017-01-10", 0),
            ("S1", "2017-02-01", "2017-02-15", 1),
        ]
    )
    df[DELIVERED] = df[DELIVERED].astype("datetime64[us]")

    out = add_seller_history_features(df)

    assert out.loc[1, "seller_prior_orders"] == 1
    assert out.loc[1, "seller_prior_late_rate"] == 0.0


def test_an_order_still_in_transit_is_not_history() -> None:
    # A is purchased first but delivered *after* B is placed, so B must not see it.
    df = _orders(
        [
            ("S1", "2017-01-01", "2017-03-20", 1),
            ("S1", "2017-02-01", "2017-02-15", 0),
        ]
    )
    out = add_seller_history_features(df)
    assert out.loc[1, "seller_prior_orders"] == 0
    assert pd.isna(out.loc[1, "seller_prior_late_rate"])


def test_history_never_includes_the_current_order() -> None:
    df = _orders(
        [
            ("S1", "2017-01-01", "2017-01-05", 1),
            ("S1", "2017-01-10", "2017-01-15", 1),
            ("S1", "2017-01-20", "2017-01-25", 1),
        ]
    )
    out = add_seller_history_features(df)
    assert list(out["seller_prior_orders"]) == [0.0, 1.0, 2.0]
    # Two prior orders, both late — the rate would be 1.0 only if the current row leaked in.
    assert out.loc[2, "seller_prior_late_rate"] == 1.0
    assert out.loc[1, "seller_prior_late_rate"] == 1.0


def test_history_is_scoped_per_seller() -> None:
    df = _orders(
        [
            ("S1", "2017-01-01", "2017-01-05", 1),
            ("S2", "2017-01-02", "2017-01-06", 0),
            ("S2", "2017-02-01", "2017-02-05", 0),
        ]
    )
    out = add_seller_history_features(df)
    assert out.loc[2, "seller_prior_orders"] == 1
    assert out.loc[2, "seller_prior_late_rate"] == 0.0


def test_history_is_invariant_to_future_orders() -> None:
    """Rebuilding the features on the first n rows must reproduce the same values.

    This is the property that matters: the feature for an order cannot depend on anything that
    happened after it. Adding, relabelling or deleting later orders must not move it.
    """
    df = _orders(
        [
            ("S1", "2017-01-01", "2017-01-08", 0),
            ("S1", "2017-01-05", "2017-01-20", 1),
            ("S2", "2017-01-06", "2017-01-09", 0),
            ("S1", "2017-01-25", "2017-02-02", 1),
            ("S2", "2017-01-28", "2017-02-01", 1),
            ("S1", "2017-02-05", "2017-02-12", 0),
        ]
    )
    full = add_seller_history_features(df)

    for n in range(1, len(df) + 1):
        partial = add_seller_history_features(df.iloc[:n])
        for column in (*HISTORY_FEATURES, "seller_has_history"):
            got = partial[column].iloc[-1]
            expected = full[column].iloc[n - 1]
            if pd.isna(expected):
                assert pd.isna(got), (
                    f"{column} row {n - 1} gained a value when later orders were added"
                )
            else:
                assert got == expected, f"{column} row {n - 1} changed when later orders were added"


def test_relabelling_a_future_order_does_not_change_past_features() -> None:
    df = _orders(
        [
            ("S1", "2017-01-01", "2017-01-08", 0),
            ("S1", "2017-01-25", "2017-02-20", 1),
            ("S1", "2017-02-05", "2017-02-12", 0),
        ]
    )
    flipped = df.copy()
    flipped.loc[2, "is_late"] = True

    base = add_seller_history_features(df)
    other = add_seller_history_features(flipped)
    # The third order is placed on 2017-02-05, before the second order is delivered (02-20).
    assert base.loc[2, "seller_prior_orders"] == other.loc[2, "seller_prior_orders"] == 1.0
    assert base.loc[2, "seller_prior_late_rate"] == other.loc[2, "seller_prior_late_rate"] == 0.0


def test_rows_without_a_seller_or_delivery_date_get_no_history() -> None:
    df = _orders([("S1", "2017-01-01", "2017-01-10", 0), (np.nan, "2017-02-01", "2017-02-10", 0)])
    out = add_seller_history_features(df)
    assert out["seller_prior_orders"].notna().all()
    assert out.loc[1, "seller_prior_orders"] == 0
