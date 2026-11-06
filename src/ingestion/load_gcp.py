"""Upload the raw CSVs to GCS and load them into BigQuery.

This is the cloud counterpart of :mod:`src.ingestion.load_postgres`. Both read the same nine
CSVs and produce the same ``raw`` layer; only the destination differs.

Two behaviours matter for orchestration:

* **Idempotent.** Objects are overwritten and tables are replaced, so re-running leaves the
  cloud in the same state. Airflow retries depend on this.
* **Verified.** Every load is followed by a row-count check against the figures established in
  the data-understanding pass, so a truncated upload fails the task instead of silently
  producing a short table.

Authentication uses Application Default Credentials. A service-account keyfile is not an
option here: the project enforces ``constraints/iam.disableServiceAccountKeyCreation``.

Usage::

    uv run python -m src.ingestion.load_gcp
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import bigquery, storage
from src.config import DATA_DIR, PROJECT_ROOT, RAW_FILES
from src.ingestion.load_postgres import EXPECTED_ROWS

logger = logging.getLogger(__name__)

RAW_DATASET = "raw"
GCS_PREFIX = "raw"

#: BigQuery's CSV reader needs to be told about embedded newlines. The review comments contain
#: them, and without this flag the load fails with "Missing close quote".
LOAD_JOB_CONFIG = {
    "source_format": bigquery.SourceFormat.CSV,
    "skip_leading_rows": 1,
    "autodetect": True,
    "allow_quoted_newlines": True,
    "allow_jagged_rows": True,
    "max_bad_records": 10,
    "write_disposition": bigquery.WriteDisposition.WRITE_TRUNCATE,
}

#: The translation CSV is small and its header is not always detected, so its schema is stated
#: explicitly rather than inferred.
EXPLICIT_SCHEMAS: dict[str, list[bigquery.SchemaField]] = {
    "category_translation": [
        bigquery.SchemaField("product_category_name", "STRING"),
        bigquery.SchemaField("product_category_name_english", "STRING"),
    ],
}


def _settings() -> tuple[str, str]:
    """Return ``(project_id, bucket_name)`` from the environment."""
    load_dotenv(PROJECT_ROOT / ".env")

    project = os.environ.get("GCP_PROJECT_ID")
    bucket = os.environ.get("GCS_BUCKET_RAW")
    missing = [
        name
        for name, value in (("GCP_PROJECT_ID", project), ("GCS_BUCKET_RAW", bucket))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"missing environment variables: {', '.join(missing)}. "
            "Fill in the GCP block of .env."
        )
    return project, bucket


def upload_to_gcs(data_dir: Path | None = None) -> dict[str, str]:
    """Upload the nine raw CSVs to ``gs://<bucket>/raw/`` and return their URIs."""
    _, bucket_name = _settings()
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    source_dir = data_dir or DATA_DIR

    uris: dict[str, str] = {}
    for logical_name, filename in RAW_FILES.items():
        path = source_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"missing raw file: {path}")

        blob = bucket.blob(f"{GCS_PREFIX}/{filename}")
        blob.upload_from_filename(str(path))
        uris[logical_name] = f"gs://{bucket_name}/{GCS_PREFIX}/{filename}"
        logger.info("uploaded %s -> %s", filename, uris[logical_name])

    return uris


def load_to_bigquery(uris: dict[str, str] | None = None) -> dict[str, int]:
    """Load the uploaded CSVs into ``<project>.raw`` and return the row count per table.

    When ``uris`` is omitted the objects are read from the bucket directly, so the task can be
    re-run without re-uploading.
    """
    project, bucket_name = _settings()
    client = bigquery.Client(project=project)

    if uris is None:
        uris = {
            name: f"gs://{bucket_name}/{GCS_PREFIX}/{filename}"
            for name, filename in RAW_FILES.items()
        }

    counts: dict[str, int] = {}
    for logical_name, uri in uris.items():
        table_id = f"{project}.{RAW_DATASET}.{logical_name}"
        config = bigquery.LoadJobConfig(**LOAD_JOB_CONFIG)

        if logical_name in EXPLICIT_SCHEMAS:
            config.schema = EXPLICIT_SCHEMAS[logical_name]
            config.autodetect = False

        job = client.load_table_from_uri(uri, table_id, job_config=config)
        job.result()

        table = client.get_table(table_id)
        counts[logical_name] = table.num_rows
        logger.info("loaded %s: %s rows", table_id, f"{table.num_rows:,}")

    return counts


def verify_counts(counts: dict[str, int]) -> None:
    """Raise if any BigQuery table has an unexpected row count."""
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

    uris = upload_to_gcs()
    counts = load_to_bigquery(uris)
    verify_counts(counts)

    print()
    print(f"{'table':<22}{'rows':>12}")
    print("-" * 34)
    for name, n in counts.items():
        print(f"{name:<22}{n:>12,}")
    print("-" * 34)
    print(f"{'total':<22}{sum(counts.values()):>12,}")


if __name__ == "__main__":
    main()
