"""Reference data the prediction interface needs: real orders and the value lists for its form.

Reading this from the analytical dataset keeps the demo honest — the sample orders are actual
checkout payloads, and the dropdown values are the categories and states that appear in the
training data rather than a hand-written subset.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd
from src.config import ORDER_LEVEL_PATH

logger = logging.getLogger(__name__)

#: Fields a caller could reasonably hold at checkout. The actual delivery date, the status and
#: every review column are deliberately absent.
PAYLOAD_FIELDS: tuple[str, ...] = (
    "order_purchase_timestamp",
    "order_estimated_delivery_date",
    "customer_state",
    "seller_state",
    "product_category",
    "primary_seller_id",
    "item_revenue",
    "freight_value",
    "payment_value",
    "n_items",
    "n_sellers",
    "n_products",
    "max_installments",
    "product_weight_g",
)


def _load() -> pd.DataFrame:
    if not ORDER_LEVEL_PATH.exists():
        raise FileNotFoundError(
            f"{ORDER_LEVEL_PATH} not found — build the analytical dataset first"
        )
    return pd.read_parquet(ORDER_LEVEL_PATH)


@lru_cache(maxsize=1)
def reference_options() -> dict[str, list[str]]:
    """Distinct states and categories, most common first so the dropdowns open on a likely value."""
    orders = _load()
    states = orders["customer_state"].dropna().value_counts().index.astype(str).tolist()
    categories = orders["product_category"].dropna().value_counts().index.astype(str).tolist()
    return {"states": states, "categories": categories}


def sample_orders(count: int = 3, *, late: bool | None = None) -> list[dict]:
    """Real orders as checkout payloads.

    ``late`` filters to orders that did or did not arrive after the estimate, which is useful for
    demonstrating that the model separates them without ever seeing that fact.
    """
    orders = _load()
    labelled = orders[orders["is_late"].notna()]
    if late is True:
        labelled = labelled[labelled["is_late"].astype("boolean").fillna(False)]
    elif late is False:
        labelled = labelled[~labelled["is_late"].astype("boolean").fillna(True)]

    payloads: list[dict] = []
    for record in labelled[list(PAYLOAD_FIELDS)].head(count).to_dict("records"):
        payload: dict = {}
        for key, value in record.items():
            if value is None or (isinstance(value, float) and pd.isna(value)):
                continue
            if key in {"order_purchase_timestamp", "order_estimated_delivery_date"}:
                payload[key] = pd.Timestamp(value).isoformat()
            else:
                payload[key] = value.item() if hasattr(value, "item") else value
        payloads.append(payload)
    return payloads
