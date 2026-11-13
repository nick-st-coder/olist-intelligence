"""Assemble a model-ready feature frame from an order request.

The prediction service receives the facts an order has at checkout and must reproduce exactly
the feature frame the model was fitted on. Rather than re-deriving the calendar and lead-time
fields here, this module calls the same preprocessing functions the batch pipeline uses, so a
scored order and a trained order cannot drift apart.

Everything attached here is knowable at ``order_purchase_timestamp``:

* order composition — item counts, revenue, freight, payment value, installments
* geography — customer and seller state, cross-state flag, seller-to-customer distance
* catalogue — product category and weight
* calendar and ``estimated_lead_days`` — from the promised delivery date
* historical seller statistics — from orders the seller had already completed

No actual-delivery, carrier-handover, review or status field is read anywhere in this path.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from src.features.build_features import ALL_FEATURES, BOOLEAN_FEATURES, NUMERIC_FEATURES
from src.preprocessing.cleaning import add_purchase_time_features
from src.preprocessing.geolocation import haversine_km

logger = logging.getLogger(__name__)

#: Columns an incoming order must supply. Missing ones are filled with the documented neutral
#: value rather than rejected, because a checkout payload rarely carries all of them.
OPTIONAL_DEFAULTS: dict[str, object] = {
    "n_items": 1.0,
    "n_sellers": 1.0,
    "n_products": 1.0,
    "max_installments": 1.0,
    "product_weight_g": np.nan,
    "seller_customer_distance_km": np.nan,
}


def normalise_request(frame: pd.DataFrame) -> pd.DataFrame:
    """Coerce a request frame into the dtypes the preprocessing functions expect."""
    out = frame.copy()

    for column in ("order_purchase_timestamp", "order_estimated_delivery_date"):
        if column not in out.columns:
            raise KeyError(f"order payload missing required field: {column}")
        out[column] = pd.to_datetime(out[column], errors="coerce")

    for column in ("customer_state", "seller_state", "product_category"):
        if column in out.columns:
            # An unknown or blank label is a real category in the training data.
            out[column] = (
                out[column]
                .astype("object")
                .where(out[column].notna() & (out[column].astype(str).str.strip() != ""), "unknown")
            )

    return out


def attach_geography(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive the distance feature when coordinates are supplied.

    The service accepts either the precomputed distance or the four coordinates. Recomputing it
    from coordinates keeps the two paths consistent — the batch pipeline builds the same number
    with :func:`~src.preprocessing.geolocation.haversine_km`.
    """
    out = frame.copy()
    coords = ["customer_lat", "customer_lng", "seller_lat", "seller_lng"]

    if (
        "seller_customer_distance_km" in out.columns
        and out["seller_customer_distance_km"].notna().all()
    ):
        return out

    if not set(coords) <= set(out.columns):
        return out

    computed = haversine_km(
        out["seller_lat"], out["seller_lng"], out["customer_lat"], out["customer_lng"]
    )
    if "seller_customer_distance_km" in out.columns:
        out["seller_customer_distance_km"] = out["seller_customer_distance_km"].fillna(computed)
    else:
        out["seller_customer_distance_km"] = computed
    return out


def attach_cross_state(frame: pd.DataFrame) -> pd.DataFrame:
    """Set ``is_cross_state`` from the two states when the caller did not supply it."""
    out = frame.copy()
    if "is_cross_state" in out.columns and out["is_cross_state"].notna().all():
        return out
    if not {"customer_state", "seller_state"} <= set(out.columns):
        return out

    derived = (out["seller_state"] != out["customer_state"]).astype("boolean")
    if "is_cross_state" in out.columns:
        out["is_cross_state"] = out["is_cross_state"].astype("boolean").fillna(derived)
    else:
        out["is_cross_state"] = derived
    return out


def fill_optional(frame: pd.DataFrame) -> pd.DataFrame:
    """Fill composition fields the caller omitted with their neutral value.

    The values are deliberately benign: one item, one seller, one product. The preprocessing
    pipeline has already been fitted, so a missing numeric is imputed with the training median —
    filling here would only override that with a guess.
    """
    out = frame.copy()
    for column, default in OPTIONAL_DEFAULTS.items():
        if column not in out.columns:
            out[column] = default
        elif default is not np.nan:
            out[column] = out[column].fillna(default)
    return out


def build_serving_features(
    requests: pd.DataFrame,
    *,
    completion: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Turn one row per order into the exact feature frame :func:`predict_orders` expects.

    ``completion`` is the seller completion timeline built from delivered history. Supplying it
    lets a long-running service build the timeline once instead of per request; without it, the
    seller history columns are absent and the model falls back to the imputed median with
    ``seller_has_history`` false.
    """
    if requests.empty:
        return pd.DataFrame(columns=list(ALL_FEATURES), index=requests.index)

    out = normalise_request(requests)
    out = fill_optional(out)
    out = attach_geography(out)
    out = attach_cross_state(out)
    out = add_purchase_time_features(out)

    if completion is not None and not completion.empty and "primary_seller_id" in out.columns:
        from src.features.historical import attach_seller_history

        out = attach_seller_history(out, completion)
    elif "seller_has_history" not in out.columns:
        # No seller to look up: the order genuinely has no history, which is what the flag says.
        # The rates stay missing so the pipeline imputes them with the training median.
        out["seller_has_history"] = False

    missing = [c for c in ALL_FEATURES if c not in out.columns]
    if missing:
        logger.debug("serving frame is missing features, they will be imputed: %s", missing)
        for column in missing:
            out[column] = np.nan

    features = out[list(ALL_FEATURES)].copy()
    for column in (*BOOLEAN_FEATURES, *NUMERIC_FEATURES):
        features[column] = pd.to_numeric(features[column], errors="coerce").astype("float64")

    return features
