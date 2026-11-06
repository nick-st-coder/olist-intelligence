"""Orchestrates the raw-to-warehouse pipeline.

The DAG is deliberately thin: every step calls code that already works standalone, so a task
failure points at the module rather than at the orchestration.

Two destinations are supported and both are kept working. The local Postgres path is the
default because it needs no cloud account; the GCS/BigQuery path is enabled by setting
``OLIST_CLOUD_ENABLED=true`` in the environment. Neither replaces the other — the same nine
CSVs feed both, and the two warehouses are reconciled against each other.

Idempotency is the contract. ``load_raw`` replaces every raw table, the cloud tasks overwrite
their objects and tables, and ``dbt build`` drops and recreates its models, so running the DAG
twice leaves both warehouses in the same state. The second run is the real test of that.
"""

from __future__ import annotations

import datetime as dt
import os

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


def cloud_enabled() -> bool:
    """Whether the GCS/BigQuery branch should run."""
    return os.environ.get("OLIST_CLOUD_ENABLED", "false").lower() in {"1", "true", "yes"}


@dag(
    dag_id="olist_pipeline",
    description="Load the nine raw CSVs into Postgres and BigQuery, then build the dbt warehouse.",
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
        """Replace the local Postgres raw schema with the current CSVs."""
        from src.ingestion.load_postgres import load_all, verify_counts

        verify_counts(load_all())

    @task
    def upload_to_gcs() -> dict[str, str]:
        """Archive the nine CSVs in the raw bucket and return their URIs."""
        from src.ingestion.load_gcp import upload_to_gcs as upload

        return upload()

    @task
    def load_to_bigquery(uris: dict[str, str]) -> None:
        """Replace the BigQuery raw dataset with the uploaded CSVs."""
        from src.ingestion.load_gcp import load_to_bigquery as load
        from src.ingestion.load_gcp import verify_counts

        verify_counts(load(uris))

    dbt_build_local = BashOperator(
        task_id="dbt_build_local",
        bash_command="cd /opt/airflow/dbt && dbt build --target dev",
    )

    dbt_build_bq = BashOperator(
        task_id="dbt_build_bq",
        bash_command="cd /opt/airflow/dbt && dbt build --target bq",
    )

    counts = extract()
    validated = validate(counts)
    local = load_raw()

    # The cloud branch is skipped rather than removed when disabled, so the DAG graph stays the
    # same and the task history shows the branch was considered.
    if cloud_enabled():
        uris = upload_to_gcs()
        cloud = load_to_bigquery(uris)
        validated >> local >> dbt_build_local
        validated >> uris >> cloud >> dbt_build_bq
    else:
        validated >> local >> dbt_build_local


olist_pipeline()
