"""Single source of truth for project paths.

Every module resolves paths from here, never from the current working directory.
"""

from __future__ import annotations

from pathlib import Path


def find_project_root(start: Path | None = None) -> Path:
    """Walk upwards until a directory containing both ``data/`` and ``pyproject.toml`` is found."""
    start = (start or Path(__file__)).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "data").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(f"Could not locate project root (data/ + pyproject.toml) from {start}")


PROJECT_ROOT: Path = find_project_root()
DATA_DIR: Path = PROJECT_ROOT / "data"
INTERIM_DIR: Path = DATA_DIR / "interim"
PROCESSED_DIR: Path = DATA_DIR / "processed"

RAW_FILES: dict[str, str] = {
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "products": "olist_products_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

ORDER_LEVEL_PATH: Path = PROCESSED_DIR / "orders_enriched.parquet"

#: Columns that must never reach a model feature matrix. Enforced by ``tests/test_features.py``.
FORBIDDEN_FEATURES: frozenset[str] = frozenset(
    {
        "order_delivered_customer_date",
        "order_delivered_carrier_date",
        "order_approved_at",
        "order_estimated_delivery_date",
        "delivery_days",
        "delay_days",
        "shipping_limit_date",
        "review_score",
        "review_comment_title",
        "review_comment_message",
        "review_creation_date",
        "review_answer_timestamp",
        "order_status",
        "is_late",
    }
)
