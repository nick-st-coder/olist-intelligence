"""Cleaning and the order-level analytical dataset.

Builds exactly one row per order from the nine raw CSVs. The pipeline is:

1. parse timestamps
2. aggregate ``order_items``, ``order_payments`` and ``order_reviews`` separately to order grain
3. collapse ``geolocation`` to one point per ZIP prefix
4. ``LEFT JOIN`` everything onto ``orders`` (never ``INNER`` — 775 orders have no items)
5. derive the delivery-delay label and the distance feature

Every join asserts that the row count is unchanged, so row multiplication fails loudly rather
than silently inflating the dataset.
"""

from __future__ import annotations

import logging

import pandas as pd
from src.config import FORBIDDEN_FEATURES
from src.preprocessing.aggregation import (
    aggregate_order_items,
    aggregate_order_payments,
    aggregate_reviews,
)
from src.preprocessing.geolocation import (
    aggregate_geolocation,
    attach_coordinates,
    haversine_km,
)

logger = logging.getLogger(__name__)

TIMESTAMP_COLUMNS: tuple[str, ...] = (
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
)

#: The documented target. Strict comparison: a delivery on the estimated day counts as late.
#: ``order_estimated_delivery_date`` is date-only (100% at 00:00:00), so the end-of-day variant
#: would reclassify 1,292 orders and move the late rate from 8.11% to 6.77%. Strict is canonical.
LATE_DEFINITION = "strict"


def parse_timestamps(orders: pd.DataFrame) -> pd.DataFrame:
    """Parse the five ``orders`` timestamp columns to datetime."""
    out = orders.copy()
    for col in TIMESTAMP_COLUMNS:
        if col not in out.columns:
            raise KeyError(f"orders missing timestamp column: {col}")
        out[col] = pd.to_datetime(out[col], errors="coerce")
    return out


def add_delivery_label(orders: pd.DataFrame) -> pd.DataFrame:
    """Add ``is_late`` and ``is_label_eligible``.

    ``is_label_eligible`` is ``True`` only for ``order_status == 'delivered'`` with both the
    actual delivery date and the estimate present. 96,470 of 99,441 orders qualify.
    """
    out = orders.copy()

    out["is_label_eligible"] = (
        (out["order_status"] == "delivered")
        & out["order_delivered_customer_date"].notna()
        & out["order_estimated_delivery_date"].notna()
    )

    out["is_late"] = pd.NA
    eligible = out["is_label_eligible"]
    out.loc[eligible, "is_late"] = (
        out.loc[eligible, "order_delivered_customer_date"]
        > out.loc[eligible, "order_estimated_delivery_date"]
    )
    out["is_late"] = out["is_late"].astype("boolean")

    n_eligible = int(eligible.sum())
    n_late = int(out.loc[eligible, "is_late"].sum())
    logger.info(
        "label: %s eligible of %s orders, %s late (%.2f%%)",
        f"{n_eligible:,}",
        f"{len(out):,}",
        f"{n_late:,}",
        n_late / n_eligible * 100 if n_eligible else 0.0,
    )
    return out


def add_lead_time_features(orders: pd.DataFrame) -> pd.DataFrame:
    """Add purchase-time-knowable temporal features.

    Only ``estimated_lead_days`` is safe for the model: it is ``estimate - purchase``, both known
    at purchase. ``delivery_days`` is post-purchase and exists for diagnostics only — it is in
    :data:`src.config.FORBIDDEN_FEATURES`.
    """
    out = orders.copy()
    purchase = out["order_purchase_timestamp"]

    out["purchase_year"] = purchase.dt.year
    out["purchase_month"] = purchase.dt.month
    out["purchase_day_of_week"] = purchase.dt.day_of_week
    out["purchase_hour"] = purchase.dt.hour
    out["purchase_is_weekend"] = purchase.dt.day_of_week >= 5

    out["estimated_lead_days"] = (out["order_estimated_delivery_date"] - purchase).dt.days

    # Diagnostics only — never a feature.
    out["delivery_days"] = (out["order_delivered_customer_date"] - purchase).dt.days

    # NOTE: ``.dt.days`` truncates towards zero, so an order delivered 59 minutes after the
    # (date-only) estimate yields delay_days == 0 while still being late. 1,292 orders are in
    # exactly that state. ``delay_hours`` keeps the sub-day resolution for diagnostics.
    out["delay_days"] = (
        out["order_delivered_customer_date"] - out["order_estimated_delivery_date"]
    ).dt.days
    out["delay_hours"] = (
        out["order_delivered_customer_date"] - out["order_estimated_delivery_date"]
    ).dt.total_seconds() / 3600

    return out


def build_order_level_dataset(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build the order-level analytical dataset: exactly one row per order.

    Parameters
    ----------
    tables
        The nine raw tables keyed by logical name, as returned by
        :func:`src.ingestion.extract.load_raw_tables`.

    Returns
    -------
    pandas.DataFrame
        One row per ``order_id``, with aggregated item/payment/review facts, customer and seller
        geography, the delivery label and the distance feature.
    """
    orders = parse_timestamps(tables["orders"])
    n_orders = len(orders)

    items_agg = aggregate_order_items(tables["order_items"])
    payments_agg = aggregate_order_payments(tables["order_payments"])
    reviews_agg = aggregate_reviews(tables["order_reviews"])

    geo_agg = aggregate_geolocation(tables["geolocation"])

    customers = tables["customers"][
        [
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        ]
    ].copy()
    customers = attach_coordinates(customers, geo_agg, "customer_zip_code_prefix", "customer")

    sellers = tables["sellers"][
        ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"]
    ].copy()
    sellers = attach_coordinates(sellers, geo_agg, "seller_zip_code_prefix", "seller")

    products = tables["products"].merge(
        tables["category_translation"], on="product_category_name", how="left"
    )
    products["product_category"] = (
        products["product_category_name_english"]
        .fillna(products["product_category_name"])
        .fillna("unknown")
    )

    # Primary seller per order: the seller of the first line item. 1,278 orders have more than
    # one seller; the primary seller is used for the distance feature and the rest are counted.
    primary_seller = (
        tables["order_items"]
        .sort_values(["order_id", "order_item_id"])
        .drop_duplicates("order_id", keep="first")[["order_id", "seller_id", "product_id"]]
        .rename(columns={"seller_id": "primary_seller_id", "product_id": "primary_product_id"})
    )

    df = orders.merge(items_agg, on="order_id", how="left")
    _assert_row_count(df, n_orders, "order_items")

    df = df.merge(payments_agg, on="order_id", how="left")
    _assert_row_count(df, n_orders, "order_payments")

    df = df.merge(reviews_agg, on="order_id", how="left")
    _assert_row_count(df, n_orders, "order_reviews")

    df = df.merge(customers, on="customer_id", how="left")
    _assert_row_count(df, n_orders, "customers")

    df = df.merge(primary_seller, on="order_id", how="left")
    _assert_row_count(df, n_orders, "primary_seller")

    df = df.merge(
        sellers[
            [
                "seller_id",
                "seller_state",
                "seller_city",
                "seller_lat",
                "seller_lng",
            ]
        ].rename(columns={"seller_id": "primary_seller_id"}),
        on="primary_seller_id",
        how="left",
    )
    _assert_row_count(df, n_orders, "sellers")

    df = df.merge(
        products[["product_id", "product_category", "product_weight_g"]].rename(
            columns={"product_id": "primary_product_id"}
        ),
        on="primary_product_id",
        how="left",
    )
    _assert_row_count(df, n_orders, "products")

    df["seller_customer_distance_km"] = haversine_km(
        df["seller_lat"], df["seller_lng"], df["customer_lat"], df["customer_lng"]
    )
    df["is_cross_state"] = df["seller_state"] != df["customer_state"]

    df = add_delivery_label(df)
    df = add_lead_time_features(df)

    if df["order_id"].duplicated().any():
        raise AssertionError("order-level dataset has duplicate order_id values")

    logger.info("order-level dataset: %s rows x %s columns", f"{len(df):,}", df.shape[1])
    return df


def _assert_row_count(df: pd.DataFrame, expected: int, step: str) -> None:
    if len(df) != expected:
        raise AssertionError(
            f"row multiplication after joining {step}: {len(df):,} rows != {expected:,} orders"
        )


def select_model_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return only the columns that are legal model inputs.

    Drops every column in :data:`src.config.FORBIDDEN_FEATURES` plus identifiers. This is the
    single place the leakage contract is enforced in code.
    """
    drop = set(FORBIDDEN_FEATURES) | {
        "order_id",
        "customer_id",
        "customer_unique_id",
        "primary_seller_id",
        "primary_product_id",
        "review_id",
    }
    keep = [c for c in df.columns if c not in drop]
    return df[keep].copy()
