"""Load the nine raw CSVs into the local Postgres ``raw`` schema.

This is the only ingestion step dbt cannot perform: dbt reads tables that already exist, it
never reads files. Everything downstream of this module is SQL.

The load is idempotent — every table is replaced, so re-running produces the same database
state. That matters for the Airflow DAG, which retries tasks, and for local iteration.

Usage::

    uv run python -m src.ingestion.load_postgres
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from src.config import PROJECT_ROOT, RAW_FILES
from src.ingestion.extract import load_raw_table

logger = logging.getLogger(__name__)

RAW_SCHEMA = "raw"

#: Expected row counts, verified in the data-understanding pass. A mismatch means the CSV on
#: disk is not the file the rest of the project was built against.
EXPECTED_ROWS: dict[str, int] = {
    "orders": 99_441,
    "order_items": 112_650,
    "order_payments": 103_886,
    "order_reviews": 99_224,
    "customers": 99_441,
    "sellers": 3_095,
    "products": 32_951,
    "geolocation": 1_000_163,
    "category_translation": 71,
}


def table_name(logical_name: str) -> str:
    """Map a logical table name to its Postgres table name.

    ``order_payments`` becomes ``order_payments`` — the ``olist_`` prefix and ``_dataset``
    suffix are stripped from the filename, not from the logical name, so the two agree.
    """
    return logical_name


def get_engine() -> Engine:
    """Build a SQLAlchemy engine from the ``POSTGRES_*`` environment variables."""
    load_dotenv(PROJECT_ROOT / ".env")

    required = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        raise RuntimeError(
            f"missing environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the Postgres block."
        )

    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
        f"{os.environ['POSTGRES_PASSWORD']}@"
        f"{os.environ.get('POSTGRES_HOST', 'localhost')}:"
        f"{os.environ.get('POSTGRES_PORT', '5432')}/"
        f"{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url, pool_pre_ping=True)


def ensure_schema(engine: Engine, schema: str = RAW_SCHEMA) -> None:
    """Create the ``raw`` schema if it does not exist."""
    with engine.begin() as conn:
        conn.execute(text(f"create schema if not exists {schema}"))
    logger.info("schema %s ready", schema)


def drop_downstream_schemas(engine: Engine) -> None:
    """Drop the dbt-built schemas so the raw tables can be replaced.

    Replacing a raw table means ``DROP TABLE``, which fails while dbt views depend on it. The
    dbt schemas are derived artefacts and are rebuilt by ``dbt build``, so dropping them here
    keeps the load idempotent without weakening the raw layer.
    """
    with engine.begin() as conn:
        for schema in ("staging", "analytics"):
            conn.execute(text(f"drop schema if exists {schema} cascade"))
    logger.info("dropped derived schemas: staging, analytics")


def load_table(
    engine: Engine,
    logical_name: str,
    data_dir: Path | None = None,
    schema: str = RAW_SCHEMA,
) -> int:
    """Load one raw CSV into ``schema.<logical_name>`` and return the row count.

    The table is replaced, never appended to, so the load is idempotent.
    """
    df = load_raw_table(logical_name, data_dir)
    target = table_name(logical_name)

    df.to_sql(
        target,
        engine,
        schema=schema,
        if_exists="replace",
        index=False,
        chunksize=5_000,
    )
    logger.info("loaded %s.%s: %s rows x %s cols", schema, target, f"{len(df):,}", df.shape[1])
    return len(df)


def load_all(data_dir: Path | None = None) -> dict[str, int]:
    """Load all nine raw CSVs and return the row count per logical table name."""
    engine = get_engine()
    ensure_schema(engine)
    drop_downstream_schemas(engine)

    counts: dict[str, int] = {}
    for logical_name in RAW_FILES:
        counts[logical_name] = load_table(engine, logical_name, data_dir)

    return counts


def verify_counts(counts: dict[str, int]) -> None:
    """Raise if any loaded table has an unexpected row count."""
    problems = [
        f"{name}: expected {expected:,}, got {counts.get(name, 0):,}"
        for name, expected in EXPECTED_ROWS.items()
        if counts.get(name) != expected
    ]
    if problems:
        raise AssertionError("row count mismatch after load:\n  " + "\n  ".join(problems))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    counts = load_all()
    verify_counts(counts)

    print()
    print(f"{'table':<22}{'rows':>12}")
    print("-" * 34)
    for name, n in counts.items():
        print(f"{name:<22}{n:>12,}")
    print("-" * 34)
    print(f"{'total':<22}{sum(counts.values()):>12,}")
    print()
    print(f"loaded into schema '{RAW_SCHEMA}'")


if __name__ == "__main__":
    main()
