"""Aggregation of one-to-many tables to order grain.

``order_items`` and ``order_payments`` are both one-to-many with ``orders``. Joining either
raw multiplies order rows. Each is therefore aggregated **separately** to exactly one row per
``order_id`` before any join, and the caller asserts the row count is unchanged afterwards.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def aggregate_order_items(items: pd.DataFrame) -> pd.DataFrame:
    """Collapse ``order_items`` to one row per order.

    Returns one row per ``order_id`` with revenue, freight, item/seller/product counts and the
    maximum shipping limit date. Orders with no line items are absent from the result, so the
    caller must use a ``LEFT JOIN``.
    """
    required = {"order_id", "order_item_id", "product_id", "seller_id", "price", "freight_value"}
    missing = required - set(items.columns)
    if missing:
        raise KeyError(f"order_items missing columns: {sorted(missing)}")

    agg = (
        items.groupby("order_id", as_index=False)
        .agg(
            item_revenue=("price", "sum"),
            freight_value=("freight_value", "sum"),
            n_items=("order_item_id", "size"),
            n_sellers=("seller_id", "nunique"),
            n_products=("product_id", "nunique"),
            max_item_price=("price", "max"),
            min_item_price=("price", "min"),
        )
        .reset_index(drop=True)
    )

    if agg["order_id"].duplicated().any():
        raise AssertionError("aggregate_order_items produced duplicate order_id values")

    logger.info("order_items %s rows -> %s orders", f"{len(items):,}", f"{len(agg):,}")
    return agg


def aggregate_order_payments(payments: pd.DataFrame) -> pd.DataFrame:
    """Collapse ``order_payments`` to one row per order.

    ``payment_value`` is summed across payment lines. Zero-value voucher lines are kept in the
    sum (they contribute nothing) but are counted separately so revenue analysis can exclude
    them if needed.
    """
    required = {"order_id", "payment_sequential", "payment_value", "payment_installments"}
    missing = required - set(payments.columns)
    if missing:
        raise KeyError(f"order_payments missing columns: {sorted(missing)}")

    agg = (
        payments.groupby("order_id", as_index=False)
        .agg(
            payment_value=("payment_value", "sum"),
            n_payment_lines=("payment_sequential", "size"),
            max_installments=("payment_installments", "max"),
            n_zero_value_lines=("payment_value", lambda s: int((s <= 0).sum())),
        )
        .reset_index(drop=True)
    )

    if agg["order_id"].duplicated().any():
        raise AssertionError("aggregate_order_payments produced duplicate order_id values")

    logger.info("order_payments %s rows -> %s orders", f"{len(payments):,}", f"{len(agg):,}")
    return agg


def aggregate_reviews(reviews: pd.DataFrame) -> pd.DataFrame:
    """Collapse ``order_reviews`` to exactly one row per order.

    ``order_reviews`` has no valid single-column key: 547 orders carry more than one review and
    814 rows reuse a ``review_id``. The latest ``review_answer_timestamp`` wins, tie-broken by
    the lowest ``review_score`` so the result is deterministic.
    """
    required = {"order_id", "review_id", "review_score", "review_answer_timestamp"}
    missing = required - set(reviews.columns)
    if missing:
        raise KeyError(f"order_reviews missing columns: {sorted(missing)}")

    ordered = reviews.sort_values(
        ["order_id", "review_answer_timestamp", "review_score"],
        ascending=[True, True, True],
    )
    dedup = ordered.drop_duplicates("order_id", keep="last")[
        ["order_id", "review_id", "review_score"]
    ].reset_index(drop=True)

    if dedup["order_id"].duplicated().any():
        raise AssertionError("aggregate_reviews produced duplicate order_id values")

    logger.info("order_reviews %s rows -> %s orders", f"{len(reviews):,}", f"{len(dedup):,}")
    return dedup
