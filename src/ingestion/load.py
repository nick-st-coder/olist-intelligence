"""Persist the order-level dataset.

Parquet is used rather than CSV: it preserves dtypes (so ``is_late`` stays nullable boolean and
timestamps stay timestamps), it is roughly 10x smaller, and BigQuery loads Parquet natively in
Phase 4. The output lives in ``data/processed/`` which is gitignored.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from src.config import ORDER_LEVEL_PATH, PROCESSED_DIR

logger = logging.getLogger(__name__)


def save_order_level(df: pd.DataFrame, path: Path | None = None) -> Path:
    """Write the order-level dataset to Parquet and return the path written."""
    target = path or ORDER_LEVEL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target, index=False, compression="snappy")
    logger.info(
        "wrote %s (%s rows, %.2f MB)", target, f"{len(df):,}", target.stat().st_size / 1024**2
    )
    return target


def load_order_level(path: Path | None = None) -> pd.DataFrame:
    """Read the order-level dataset back from Parquet."""
    target = path or ORDER_LEVEL_PATH
    if not target.is_file():
        raise FileNotFoundError(
            f"{target} not found — run `uv run python -m src.preprocessing.cleaning` first"
        )
    return pd.read_parquet(target)


def ensure_processed_dir() -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    return PROCESSED_DIR
