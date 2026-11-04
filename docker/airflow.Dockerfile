FROM apache/airflow:2.9.3-python3.11

USER airflow

# Airflow 2.9.3 pins SQLAlchemy to 1.4.x. Installing the project's dependencies without a
# constraint file upgrades SQLAlchemy to 2.x and breaks Airflow's ORM at import time, so the
# official constraints file keeps every shared dependency on a compatible version.
ARG AIRFLOW_VERSION=2.9.3
ARG PYTHON_VERSION=3.11
ARG CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

# Only what the DAG actually imports: pandas for extraction, sqlalchemy/psycopg for the load,
# python-dotenv for .env, and dbt for the transformation step. pandera and pyarrow are not
# needed here because the DAG never runs the validation or Parquet code paths.
RUN pip install --no-cache-dir --constraint "${CONSTRAINT_URL}" \
    "pandas" \
    "python-dotenv" \
    "sqlalchemy" \
    "psycopg2-binary"

# dbt is installed without the Airflow constraints file: the constraints pin dbt-core to 1.8,
# but the project's YAML uses the generic-test `arguments` syntax introduced in 1.10. dbt does
# not depend on SQLAlchemy, so Airflow's pinned 1.4.x is restored afterwards to keep its ORM
# working.
RUN pip install --no-cache-dir "dbt-core>=1.10" "dbt-postgres>=1.9" \
    && pip install --no-cache-dir "sqlalchemy<2"
