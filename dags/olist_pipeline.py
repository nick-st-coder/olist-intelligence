"""Orchestrates the raw-to-warehouse pipeline.

The DAG is deliberately thin: every step calls code that already works standalone, so a task
failure points at the module rather than at the orchestration.

Idempotency is the contract. ``load_raw`` replaces every raw table and ``dbt build`` drops and
recreates its models, so running the DAG twice leaves the warehouse in the same state. The
second run is the real test of that.
"""

from __future__ import annotations

import datetime as dt

from airflow.decorators import dag, task
from airflow.operators.bash import BashOperator

DEFAULT_ARGS = {
    "owner": "olist",
    "retries": 2,
    "retry_delay": dt.timedelta(minutes=2),
}

#: Row counts verified in the data-understanding pass. A mismatch means the CSV on disk is not
#: the file the rest of the project was built against, so the pipeline stops before loading.
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


@dag(
    dag_id="olist_pipeline",
    description="Load the nine raw CSVs into Postgres and build the dbt warehouse.",
    schedule="@daily",
    start_date=dt.datetime(2026, 1, 1),
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["olist"],
)
def olist_pipeline():
    @task
    def extract() -> dict[str, int]:
        """Read the nine CSVs and return row counts as the validation contract."""
        from src.ingestion.extract import load_raw_tables

        return {name: len(df) for name, df in load_raw_tables().items()}

    @task
    def validate(counts: dict[str, int]) -> None:
        """Fail before loading if a source file is missing or truncated."""
        problems = [
            f"{name}: expected {expected:,}, got {counts.get(name, 0):,}"
            for name, expected in EXPECTED_ROWS.items()
            if counts.get(name) != expected
        ]
        if problems:
            raise ValueError("source row count mismatch:\n  " + "\n  ".join(problems))

    @task
    def load_raw() -> None:
        """Replace the raw schema with the current CSVs."""
        from src.ingestion.load_postgres import load_all, verify_counts

        verify_counts(load_all())

    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command="cd /opt/airflow/dbt && dbt build --target dev",
    )

    counts = extract()
    validate(counts) >> load_raw() >> dbt_build


olist_pipeline()
