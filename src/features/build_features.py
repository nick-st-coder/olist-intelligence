"""Feature building for the delivery-delay model.

Phase 3 scope: the leakage contract and the purchase-time-knowable features. Historical
seller statistics (expanding windows shifted by one order) belong to Phase 6, because they
require the chronological split to be defined first.
"""

from __future__ import annotations

import logging

import pandas as pd
from src.config import FORBIDDEN_FEATURES

logger = logging.getLogger(__name__)

#: Columns that are legal model inputs. Everything else is dropped.
NUMERIC_FEATURES: tuple[str, ...] = (
    "n_items",
    "n_sellers",
    "n_products",
    "item_revenue",
    "freight_value",
    "payment_value",
    "max_installments",
    "estimated_lead_days",
    "seller_customer_distance_km",
    "product_weight_g",
    "purchase_month",
    "purchase_day_of_week",
    "purchase_hour",
)

BOOLEAN_FEATURES: tuple[str, ...] = (
    "purchase_is_weekend",
    "is_cross_state",
)

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "customer_state",
    "seller_state",
    "product_category",
)


def assert_no_leakage(columns: list[str] | tuple[str, ...]) -> None:
    """Raise if any forbidden column is present. Called before every training run."""
    leaked = FORBIDDEN_FEATURES & set(columns)
    if leaked:
        raise ValueError(f"leakage detected — forbidden columns present: {sorted(leaked)}")


def build_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Select the model feature columns from the order-level dataset.

    Only rows with a label are returned, so the frame is directly trainable.
    """
    declared = {*NUMERIC_FEATURES, *BOOLEAN_FEATURES, *CATEGORICAL_FEATURES}
    available = [
        c for c in (*NUMERIC_FEATURES, *BOOLEAN_FEATURES, *CATEGORICAL_FEATURES) if c in df.columns
    ]
    missing = declared - set(available)
    if missing:
        logger.warning("feature columns absent from the dataset: %s", sorted(missing))

    assert_no_leakage(available)

    labelled = df[df["is_late"].notna()].copy()
    features = labelled[available].copy()

    logger.info(
        "feature frame: %s rows x %s columns (%s numeric, %s boolean, %s categorical)",
        f"{len(features):,}",
        features.shape[1],
        len([c for c in available if c in NUMERIC_FEATURES]),
        len([c for c in available if c in BOOLEAN_FEATURES]),
        len([c for c in available if c in CATEGORICAL_FEATURES]),
    )
    return features


def build_target(df: pd.DataFrame) -> pd.Series:
    """Return the ``is_late`` label for the labelled rows, as a plain int series."""
    labelled = df[df["is_late"].notna()]
    return labelled["is_late"].astype(int).rename("is_late")
