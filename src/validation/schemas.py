"""Pandera schemas for the order-level analytical dataset.

Validation happens at the boundary: before the dataset is written to ``data/processed/`` and
before it enters the warehouse or the model. A schema failure is a hard stop, not a warning.
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

ORDER_LEVEL_SCHEMA = DataFrameSchema(
    columns={
        "order_id": Column(str, nullable=False, unique=True),
        "customer_id": Column(str, nullable=False),
        "order_status": Column(
            str,
            checks=Check.isin(
                [
                    "delivered",
                    "shipped",
                    "canceled",
                    "unavailable",
                    "invoiced",
                    "processing",
                    "created",
                    "approved",
                ]
            ),
            nullable=False,
        ),
        "order_purchase_timestamp": Column(pa.DateTime, nullable=False),
        "order_estimated_delivery_date": Column(pa.DateTime, nullable=False),
        "is_label_eligible": Column(bool, nullable=False),
        "is_late": Column("boolean", nullable=True),
        "item_revenue": Column(float, checks=Check.ge(0), nullable=True),
        "freight_value": Column(float, checks=Check.ge(0), nullable=True),
        "payment_value": Column(float, nullable=True),
        "n_items": Column("Int64", checks=Check.ge(1), nullable=True),
        "n_sellers": Column("Int64", checks=Check.ge(1), nullable=True),
        "n_payment_lines": Column("Int64", checks=Check.ge(1), nullable=True),
        "review_score": Column("Int64", checks=Check.in_range(1, 5), nullable=True),
        "customer_state": Column(str, nullable=False),
        "seller_state": Column(str, nullable=True),
        "product_category": Column(str, nullable=True),
        "estimated_lead_days": Column("Int64", nullable=True),
        "seller_customer_distance_km": Column(float, checks=Check.ge(0), nullable=True),
        "is_cross_state": Column("boolean", nullable=True),
    },
    checks=[
        # The label must be null exactly when the order is not label-eligible.
        Check(
            lambda df: (df["is_late"].notna() == df["is_label_eligible"]).all(),
            error="is_late must be non-null exactly when is_label_eligible is True",
        ),
        # The label must agree with the raw timestamps. Compared on the timestamps themselves,
        # NOT on delay_days: delay_days is truncated to whole days, so the 1,292 orders delivered
        # later on the estimated day have delay_days == 0 while still being late by minutes.
        Check(
            lambda df: (
                (
                    df.loc[df["is_late"] == True, "order_delivered_customer_date"]  # noqa: E712
                    > df.loc[df["is_late"] == True, "order_estimated_delivery_date"]  # noqa: E712
                ).all()
            ),
            error="every late order must have delivered_customer_date > estimated_delivery_date",
        ),
        Check(
            lambda df: (
                (
                    df.loc[df["is_late"] == False, "order_delivered_customer_date"]  # noqa: E712
                    <= df.loc[df["is_late"] == False, "order_estimated_delivery_date"]  # noqa: E712
                ).all()
            ),
            error="every on-time order must have delivered_customer_date <= estimated_delivery_date",
        ),
    ],
    strict=False,
    coerce=True,
)


def validate_order_level(df, *, lazy: bool = False):
    """Validate the order-level dataset against :data:`ORDER_LEVEL_SCHEMA`."""
    return ORDER_LEVEL_SCHEMA.validate(df, lazy=lazy)
