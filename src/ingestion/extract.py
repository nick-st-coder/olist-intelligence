"""Raw CSV extraction.

The raw files in ``data/`` are the read-only source of truth. Nothing here writes to them.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from src.config import DATA_DIR, RAW_FILES

logger = logging.getLogger(__name__)


def load_raw_table(name: str, data_dir: Path | None = None) -> pd.DataFrame:
    """Load one raw CSV by logical name (a key of :data:`src.config.RAW_FILES`)."""
    if name not in RAW_FILES:
        raise KeyError(f"unknown table {name!r}; expected one of {sorted(RAW_FILES)}")

    path = (data_dir or DATA_DIR) / RAW_FILES[name]
    if not path.is_file():
        raise FileNotFoundError(f"missing raw file: {path}")

    df = pd.read_csv(path)
    logger.info("loaded %s: %s rows x %s cols", name, f"{len(df):,}", df.shape[1])
    return df


def load_raw_tables(data_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """Load all nine raw CSVs into a dict keyed by logical table name."""
    return {name: load_raw_table(name, data_dir) for name in RAW_FILES}
