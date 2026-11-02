"""Build and persist the order-level analytical dataset.

Usage::

    uv run python -m src.preprocessing.cleaning
"""

from __future__ import annotations

import logging

from src.ingestion.extract import load_raw_tables
from src.ingestion.load import save_order_level
from src.preprocessing.cleaning import build_order_level_dataset
from src.validation.schemas import validate_order_level

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    tables = load_raw_tables()
    df = build_order_level_dataset(tables)

    validate_order_level(df)
    logger.info("schema validation passed")

    path = save_order_level(df)

    eligible = int(df["is_label_eligible"].sum())
    late = int(df.loc[df["is_label_eligible"], "is_late"].sum())
    print()
    print(f"rows                : {len(df):,}")
    print(f"columns             : {df.shape[1]}")
    print(f"label-eligible      : {eligible:,} ({eligible / len(df) * 100:.2f}%)")
    print(f"late                : {late:,} ({late / eligible * 100:.2f}% of eligible)")
    print(f"distance available  : {int(df['seller_customer_distance_km'].notna().sum()):,}")
    print(f"written to          : {path}")


if __name__ == "__main__":
    main()
