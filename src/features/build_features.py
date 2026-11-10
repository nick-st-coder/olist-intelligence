"""Feature building for the delivery-delay model.

Every column declared here is knowable at ``order_purchase_timestamp``:

* order attributes and aggregates — known once the basket is placed
* geographic attributes and the seller↔customer distance — known from the addresses
* calendar fields and ``estimated_lead_days`` — known at checkout
* historical seller statistics — computed from orders the seller had **completed** before this
  purchase, see :mod:`src.features.historical`

Delivery, carrier-handover, review and status fields are excluded in
:data:`src.config.FORBIDDEN_FEATURES` and rejected by :func:`assert_no_leakage`.
"""

from __future__ import annotations

import logging

import pandas as pd
from src.config import FORBIDDEN_FEATURES
from src.features.historical import HISTORY_FEATURES, add_seller_history_features

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
    *HISTORY_FEATURES,
)

BOOLEAN_FEATURES: tuple[str, ...] = (
    "purchase_is_weekend",
    "is_cross_state",
    "seller_has_history",
)

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "customer_state",
    "seller_state",
    "product_category",
)

ALL_FEATURES: tuple[str, ...] = (
    *NUMERIC_FEATURES,
    *BOOLEAN_FEATURES,
    *CATEGORICAL_FEATURES,
)


def assert_no_leakage(columns: list[str] | tuple[str, ...]) -> None:
    """Raise if any forbidden column is present. Called before every training run."""
    leaked = FORBIDDEN_FEATURES & set(columns)
    if leaked:
        raise ValueError(f"leakage detected — forbidden columns present: {sorted(leaked)}")


def build_feature_frame(df: pd.DataFrame, *, with_history: bool = True) -> pd.DataFrame:
    """Select the model feature columns from the order-level dataset.

    Only rows with a label are returned, so the frame is directly trainable. The seller-history
    features are added here rather than assumed to be present, so the function works on the
    dataset produced by the preprocessing step alone.
    """
    assert_no_leakage(ALL_FEATURES)

    labelled = df[df["is_late"].notna()].copy()
    if with_history and not set(HISTORY_FEATURES) <= set(labelled.columns):
        labelled = add_seller_history_features(labelled)

    available = [c for c in ALL_FEATURES if c in labelled.columns]
    missing = set(ALL_FEATURES) - set(available)
    if missing:
        logger.warning("feature columns absent from the dataset: %s", sorted(missing))

    assert_no_leakage(available)

    features = labelled[available].copy()
    # Booleans and integers become floats here rather than inside the pipeline: ``SimpleImputer``
    # rejects ``bool`` dtype, a custom casting transformer would make the saved model
    # unserialisable, and an integer column cannot represent a missing value at serving time.
    for column in (*BOOLEAN_FEATURES, *NUMERIC_FEATURES):
        if column in features.columns:
            features[column] = features[column].astype("float64")

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


#: Non-feature columns carried alongside the features for splitting and error analysis.
CONTEXT_COLUMNS: tuple[str, ...] = (
    "order_id",
    "order_purchase_timestamp",
    "is_late",
)


def build_modelling_frame(df: pd.DataFrame, *, with_history: bool = True) -> pd.DataFrame:
    """Features plus the columns needed to split by time and inspect errors.

    Built as one frame so features, label and timestamp can never drift out of alignment —
    every row keeps its own ``order_id`` and ``order_purchase_timestamp``.
    """
    features = build_feature_frame(df, with_history=with_history)
    context = df.loc[features.index, [c for c in CONTEXT_COLUMNS if c in df.columns]]
    frame = context.join(features)
    frame["is_late"] = frame["is_late"].astype(int)
    logger.info("modelling frame: %s rows x %s columns", f"{len(frame):,}", frame.shape[1])
    return frame
