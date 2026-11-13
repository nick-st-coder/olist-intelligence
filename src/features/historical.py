"""Historical seller features, computed only from orders that had already finished.

A prediction is made at ``order_purchase_timestamp``, so a seller statistic is usable only if
every order behind it had **already been delivered** by that instant. Knowledge of a prior
order's outcome arrives on its delivery date, not on its purchase date, and the median delivery
takes ten days. The features here therefore merge on the *completion* timeline rather than the
purchase timeline: for each order, they summarise the seller's orders completed strictly before
it was placed.

That definition is invariant to the future: adding, removing or relabelling orders that were
purchased later cannot change the value assigned to an earlier order. ``tests/test_historical.py``
asserts exactly that.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SELLER_COLUMN = "primary_seller_id"
PURCHASE_COLUMN = "order_purchase_timestamp"
DELIVERED_COLUMN = "order_delivered_customer_date"
LABEL_COLUMN = "is_late"

HISTORY_FEATURES: tuple[str, ...] = (
    "seller_prior_orders",
    "seller_prior_late_rate",
    "seller_prior_mean_delivery_days",
)

_HISTORY_FLAG = "seller_has_history"


def add_seller_history_features(
    order_level: pd.DataFrame,
    *,
    seller_column: str = SELLER_COLUMN,
    purchase_column: str = PURCHASE_COLUMN,
    delivered_column: str = DELIVERED_COLUMN,
    label_column: str = LABEL_COLUMN,
) -> pd.DataFrame:
    """Append the seller's completed-order history as of each order's purchase timestamp.

    Adds :data:`HISTORY_FEATURES` plus ``seller_has_history``. Sellers with no completed order
    yet receive ``prior_orders = 0`` and ``NaN`` rates, which the preprocessing pipeline
    imputes; the flag lets the model separate "no history" from "average history".
    """
    required = {seller_column, purchase_column, delivered_column, label_column}
    missing = required - set(order_level.columns)
    if missing:
        raise KeyError(f"order-level dataset missing columns: {sorted(missing)}")

    completion = build_completion_timeline(
        order_level,
        seller_column=seller_column,
        purchase_column=purchase_column,
        delivered_column=delivered_column,
        label_column=label_column,
    )
    out = attach_seller_history(
        order_level,
        completion,
        seller_column=seller_column,
        purchase_column=purchase_column,
    )

    logger.info(
        "seller history: %s of %s orders have at least one completed prior order by the same seller",
        f"{int(out[_HISTORY_FLAG].sum()):,}",
        f"{len(out):,}",
    )
    return out


def build_completion_timeline(
    order_level: pd.DataFrame,
    *,
    seller_column: str = SELLER_COLUMN,
    purchase_column: str = PURCHASE_COLUMN,
    delivered_column: str = DELIVERED_COLUMN,
    label_column: str = LABEL_COLUMN,
) -> pd.DataFrame:
    """The reusable half of :func:`add_seller_history_features`.

    Building the timeline is the expensive step and it depends only on completed orders, so a
    caller that scores many new orders can build it once and reuse it across batches — which is
    what the prediction service does at start-up.
    """
    return _completion_timeline(
        order_level, seller_column, purchase_column, delivered_column, label_column
    )


def attach_seller_history(
    df: pd.DataFrame,
    completion: pd.DataFrame,
    *,
    seller_column: str = SELLER_COLUMN,
    purchase_column: str = PURCHASE_COLUMN,
) -> pd.DataFrame:
    """Attach the as-of-purchase seller statistics from a prebuilt completion timeline.

    Works on any frame that carries a seller column and a purchase timestamp — training rows or
    a batch of orders awaiting a prediction. Rows whose seller has no completed order keep
    ``prior_orders = 0``, ``NaN`` rates and ``seller_has_history = False``.
    """
    stats = _as_of_purchase(df, completion, seller_column, purchase_column)
    out = df.copy()
    for col in (*HISTORY_FEATURES, _HISTORY_FLAG):
        out[col] = stats[col].to_numpy()
    return out


def _completion_timeline(
    df: pd.DataFrame,
    seller_column: str,
    purchase_column: str,
    delivered_column: str,
    label_column: str,
) -> pd.DataFrame:
    """One row per completed order, carrying the seller's running totals **including** it.

    The totals are inclusive because the as-of merge below lands on the last order the seller
    had completed before the purchase — and that order's outcome is part of the history.
    """
    timeline = pd.DataFrame(
        {
            "seller_id": df[seller_column].astype("object"),
            "completed_at": df[delivered_column],
            "late": df[label_column].astype("Float64").astype("float64"),
            "duration_days": (df[delivered_column] - df[purchase_column]).dt.total_seconds()
            / 86_400.0,
        }
    )
    timeline = timeline.dropna(subset=["seller_id", "completed_at", "late"])

    # Order by completion, because that is when the outcome became knowable.
    timeline = timeline.sort_values(["seller_id", "completed_at"], kind="mergesort")

    grouped = timeline.groupby("seller_id", sort=False)
    has_duration = timeline["duration_days"].notna()

    timeline["cum_orders"] = grouped.cumcount().astype("float64") + 1.0
    timeline["cum_late"] = grouped["late"].cumsum()
    timeline["cum_duration_sum"] = (
        timeline["duration_days"].fillna(0.0).groupby(timeline["seller_id"]).cumsum()
    )
    timeline["cum_duration_n"] = has_duration.groupby(timeline["seller_id"]).cumsum()

    with np.errstate(invalid="ignore", divide="ignore"):
        timeline["seller_prior_late_rate"] = timeline["cum_late"] / timeline["cum_orders"]
        timeline["seller_prior_mean_delivery_days"] = np.where(
            timeline["cum_duration_n"] > 0,
            timeline["cum_duration_sum"] / timeline["cum_duration_n"],
            np.nan,
        )

    return timeline[
        [
            "seller_id",
            "completed_at",
            "cum_orders",
            "seller_prior_late_rate",
            "seller_prior_mean_delivery_days",
        ]
    ]


def _as_of_purchase(
    df: pd.DataFrame,
    completion: pd.DataFrame,
    seller_column: str,
    purchase_column: str,
) -> pd.DataFrame:
    """Attach the most recent completion strictly before each purchase timestamp.

    Returns one row per input row, aligned to ``df.index``. The row identity is carried through a
    private column because ``merge_asof`` reorders its output by the merge key.
    """
    if completion.empty or df.empty:
        empty = pd.DataFrame(index=df.index)
        empty["seller_prior_orders"] = 0.0
        empty["seller_prior_late_rate"] = np.nan
        empty["seller_prior_mean_delivery_days"] = np.nan
        empty[_HISTORY_FLAG] = False
        return empty

    row_ids = np.arange(len(df))
    left = (
        pd.DataFrame(
            {
                "_row_id": row_ids,
                "seller_id": df[seller_column].to_numpy(),
                purchase_column: df[purchase_column].to_numpy(),
            },
            index=df.index,
        )
        .dropna(subset=["seller_id", purchase_column])
        .sort_values(purchase_column, kind="mergesort")
    )
    # merge_asof requires identical key dtypes; the order-level frame may carry an Arrow string
    # column while the completion timeline is object-typed.
    left["seller_id"] = left["seller_id"].astype("object")

    right = completion.sort_values("completed_at", kind="mergesort").copy()
    right["seller_id"] = right["seller_id"].astype("object")

    merged = pd.merge_asof(
        left,
        right,
        left_on=purchase_column,
        right_on="completed_at",
        by="seller_id",
        direction="backward",
        allow_exact_matches=False,
    )

    merged = merged.rename(columns={"cum_orders": "seller_prior_orders"})
    # Restore the caller's row order: merge_asof sorts by the merge key and drops non-matching
    # rows, so rows are realigned positionally through the private id.
    merged = merged.set_index("_row_id").reindex(row_ids)
    merged.index = df.index

    # Rows with no seller (or no purchase timestamp) never matched, so they have no history.
    merged["seller_prior_orders"] = merged["seller_prior_orders"].fillna(0.0)
    merged[_HISTORY_FLAG] = merged["seller_prior_orders"] > 0
    return merged[
        [
            "seller_prior_orders",
            "seller_prior_late_rate",
            "seller_prior_mean_delivery_days",
            _HISTORY_FLAG,
        ]
    ]
