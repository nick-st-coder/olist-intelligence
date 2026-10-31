---
description: "Use when working on the Olist E-commerce Intelligence Platform project (olist-intelligence workspace): data ingestion, ETL, dbt, Airflow, EDA, delivery-delay ML model, FastAPI/Streamlit deployment, MLflow, Evidently monitoring, Power BI. Contains project context, phase roadmap, conventions and guardrails."
name: "Olist Intelligence Platform"
---

# Olist E-commerce Intelligence Platform

End-to-end data platform on the **Brazilian E-Commerce Public Dataset by Olist** (~100k orders, 2016–2018, 9 relational CSVs).
Goal: ingestion → warehouse → BI → delivery-delay prediction → deployment → monitoring, built **local-first**, cloud (GCP) later.

> **Full plan lives in [`CONTEXT.md`](../../CONTEXT.md)** — tech stack, target repository tree, all 9 phases with tasks and deliverables, business questions, ML specification. Read it before planning work. This file holds only the rules and conventions.

## Workspace facts

- Raw CSVs live in `Data/` (capital D) at the repo root — **not** `data/raw/`. Treat `Data/` as read-only source of truth; never overwrite it.
- Files: `olist_orders_dataset.csv`, `olist_order_items_dataset.csv`, `olist_order_payments_dataset.csv`, `olist_order_reviews_dataset.csv`, `olist_customers_dataset.csv`, `olist_sellers_dataset.csv`, `olist_products_dataset.csv`, `olist_geolocation_dataset.csv`, `product_category_name_translation.csv`.
- Grain: `orders` = 1 row/order; `order_items` = 1 row/(order, item); `payments` = 1 row/(order, payment sequential); `reviews` = 1 row/review (can be >1 per order); `geolocation` = many rows per zip prefix.
- Target repo layout (create as phases progress): `src/{ingestion,validation,preprocessing,features,modeling,monitoring}`, `dbt/models/{staging,intermediate,marts}`, `dags/`, `api/`, `app/`, `tests/`, `notebooks/`, `configs/`, `docker/`, `.github/workflows/`. Full annotated tree in [`CONTEXT.md` §5](../../CONTEXT.md).
- `data/` (lowercase) is reserved for derived outputs (`interim/`, `processed/`). Raw stays in `Data/`.

## Phase roadmap

Track progress with `/olist-phase-status`. Do not start a phase before its predecessor's deliverables exist.
Detailed tasks and deliverables per phase: [`CONTEXT.md` §6](../../CONTEXT.md).

| # | Phase | Key deliverables |
|---|-------|------------------|
| 1 | Data understanding | data dictionary, relationships, data-quality report |
| 2 | EDA | `notebooks/02_eda.ipynb`, ≥5 documented business findings |
| 3 | Cleaning & preprocessing | reusable modules, order-level dataset (1 row/order), Pandera schemas, unit tests |
| 4 | Data engineering | GCS/BigQuery loaders, dbt staging→intermediate→marts + tests, Airflow DAG |
| 5 | BI | 3-page Power BI dashboard (Executive / Logistics / Customer), Excel report |
| 6 | ML | leakage-safe features, chronological splits, baselines→RF/LGBM/XGB, Optuna, SHAP, MLflow registry |
| 7 | Deployment | FastAPI (`/health`, `/predict`, `/model/info`, `/metrics`), Streamlit, Docker, Cloud Run |
| 8 | Monitoring | Evidently drift reports, performance monitoring, retraining workflow (simulated batches) |
| 9 | CI/CD & docs | GitHub Actions (lint, test, dbt test, build, deploy), full README |

## Non-negotiable rules

1. **No leakage.** The ML target is `is_late = order_delivered_customer_date > order_estimated_delivery_date`, predicted **at purchase time**. Never use: actual delivery date, carrier handover date, delivery duration, review score/comments, order status, or any post-purchase field. Historical seller features must be computed with expanding/rolling windows strictly **before** the current order's `order_purchase_timestamp`.
2. **One row per order** in the ML dataset. Aggregate `order_items` and `payments` **separately** before joining to `orders` — never join both raw (row multiplication).
3. **Chronological splits only** (train → validation → test by time). No `train_test_split(random_state=...)` for the delivery model.
4. **Notebooks explore, `src/` produces.** Any logic used twice moves into `src/` with a test.
5. **Secrets never in Git.** Use `.env` (gitignored) + `.env.example`. No credentials in notebooks or committed configs.
6. **Validate data at boundaries** with Pandera schemas before it enters the warehouse or the model.
7. **Local before cloud.** Everything must run via Docker Compose locally; GCP is an optional deployment target.
8. **Document assumptions** (e.g. how missing `order_delivered_customer_date` rows are handled) in the README or a decision note.

## Conventions

- Python 3.11+, `uv` for deps, `pyproject.toml` as single source of config, `ruff` for lint/format, `pytest` for tests.
- Type hints on public functions; pure functions in `src/`; config via `configs/*.yaml` or env vars, not hardcoded paths.
- Paths resolved from a single project-root constant, never relative to CWD.
- SQL: `snake_case`, dbt models prefixed `stg_` / `int_` / `fact_` / `dim_`; every model has a `schema.yml` with `unique` + `not_null` tests on keys.
- Logging via `logging`, not `print`, in `src/`.
- Deterministic: fixed seeds, pinned versions, recorded dataset snapshot/version.

## Delegation

Use the specialized agents instead of doing everything inline:
`olist-data-engineer`, `olist-eda-analyst`, `olist-ml-engineer`, `olist-mlops-deployer`, `olist-reviewer`, `olist-debugger`.
Run `/olist-leakage-check` before any model training and `/olist-commit-check` before committing.
