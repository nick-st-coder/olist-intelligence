# Project Context — Olist E-commerce Intelligence Platform

> **Purpose of this file.** This is the single source of truth for *what we are building and why*.
> It captures the full project plan, tech stack, target repository structure, business questions and ML specification.
> Agent instructions in `.github/instructions/` hold the *rules*; this file holds the *plan*.
> When the plan changes, update this file first.

**Dataset:** [Brazilian E-Commerce Public Dataset by Olist — Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
**Scale:** ~100k orders, 2016–2018, 9 relational CSVs.
**Status:** Phase 1 ✅ complete — see [`notebooks/01_data_understanding.ipynb`](notebooks/01_data_understanding.ipynb) and §10 below. Phase 2 next.

---

## 1. Project overview

Build an end-to-end E-commerce Data Intelligence Platform that integrates automated data ingestion, ETL pipelines, data warehousing, business intelligence, predictive analytics, model deployment and monitoring.

The goal is a production-oriented data science + data engineering solution demonstrating the full data lifecycle: raw ingestion → business insight → production ML.

---

## 2. Business problems

### 2.1 Sales Intelligence
Understand sales performance and identify growth opportunities.

- How does revenue change over time?
- Which product categories generate the highest revenue?
- Which geographic regions generate the most sales?
- Which sellers contribute the most to revenue?
- What are the purchasing trends and seasonal patterns?
- What is the average order value?
- How do freight costs influence sales performance?

### 2.2 Logistics and Delivery Performance
Analyze delivery efficiency and the drivers of late deliveries.

- What percentage of orders arrive later than estimated?
- Which sellers have the highest delivery delay rates?
- Which regions experience the longest delivery times?
- Does shipping distance influence delivery performance?
- Which product categories are associated with longer delivery times?
- What factors contribute most to delivery delays?
- Can potentially delayed orders be identified before delivery?

### 2.3 Customer Experience
Analyze satisfaction and its drivers.

- Does late delivery correlate with lower review scores?
- Which product categories receive the worst reviews?
- Which geographic regions have the lowest customer satisfaction?
- What percentage of customers make repeat purchases?
- Which customers generate the most revenue?
- What factors influence customer satisfaction?

---

## 3. Machine learning task

### Primary task: Delivery Delay Prediction

Binary classification: will an order arrive **later than its estimated delivery date**?

| Label | Meaning |
|-------|---------|
| `1` | `order_delivered_customer_date > order_estimated_delivery_date` |
| `0` | delivered on or before the estimate |

**Prediction timestamp = `order_purchase_timestamp`.** Only information available at that instant may be used.

### Candidate features

**Order:** purchase hour, day of week, month, number of items, total order value, total freight value, number of sellers, number of unique products.
**Product:** category, weight, dimensions.
**Geographic:** customer state, seller state, approximate shipping distance.
**Historical:** seller delivery performance, seller late-delivery rate, seller order volume — computed strictly before the current order's timestamp.

### Excluded (leakage)

Actual delivery timestamp · carrier handover timestamp · actual delivery duration · review score · review comments · order status · anything unavailable at order placement.

### Model development ladder

1. `DummyClassifier` baseline
2. Logistic Regression baseline
3. Random Forest
4. LightGBM
5. XGBoost
6. Optuna hyperparameter optimization
7. Temporal cross-validation
8. Classification threshold optimization
9. SHAP explainability

### Evaluation

Precision · Recall · F1 · ROC-AUC · PR-AUC · confusion matrix · business cost-benefit analysis.
**Chronological train/validation/test splits** — never random splitting.
Primary objective: catch delayed orders while keeping the false-positive rate acceptable.

---

## 4. Technical stack

| Area | Tools |
|------|-------|
| **Programming & analysis** | Python, SQL, pandas, NumPy, matplotlib, seaborn |
| **Data engineering** | Apache Airflow (orchestration), dbt (SQL transforms), PostgreSQL (local operational DB + metadata), Pandera (validation), PySpark (optional separate exercise) |
| **Cloud (GCP)** | Cloud Storage (raw), BigQuery (warehouse), Cloud Run (API/app), Artifact Registry (images), Cloud Scheduler (optional), Cloud Logging |
| **Business intelligence** | Power BI, Excel, Power Query, DAX |
| **Machine learning** | scikit-learn, LightGBM, XGBoost, Optuna, SHAP |
| **MLOps & monitoring** | MLflow (tracking + registry), Evidently (drift), FastAPI, Docker, GitHub Actions, pytest |
| **Application** | Streamlit |
| **Dev tools** | uv, Git, GitHub, Jupyter / Marimo |

---

## 5. Target repository structure

```
olist-intelligence/
│
├── Data/                        # raw CSVs — READ ONLY (see deviation note)
│
├── data/                        # derived outputs (created in Phase 3+)
│   ├── interim/
│   └── processed/
│
├── notebooks/
│   ├── 01_data_understanding.ipynb
│   ├── 02_eda.ipynb
│   ├── 03_feature_engineering.ipynb
│   └── 04_model_experiments.ipynb
│
├── src/
│   ├── ingestion/       extract.py, load.py
│   ├── validation/      schemas.py
│   ├── preprocessing/   cleaning.py, aggregation.py
│   ├── features/        build_features.py
│   ├── modeling/        train.py, evaluate.py, predict.py
│   └── monitoring/      drift.py
│
├── dbt/
│   ├── models/
│   │   ├── staging/
│   │   ├── intermediate/
│   │   └── marts/
│   └── tests/
│
├── dags/                olist_pipeline.py
├── api/                 main.py
├── app/                 streamlit_app.py
├── tests/               test_cleaning.py, test_features.py, test_api.py
├── configs/
├── docker/
├── .github/
│   ├── workflows/
│   ├── agents/          # custom agents
│   ├── instructions/    # file instructions
│   └── prompts/         # slash-command prompts
│
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── .gitignore
├── CONTEXT.md           # this file
└── README.md
```

### Deviation from the original plan

The plan specified `data/raw/`. The CSVs are already in **`Data/`** (capital D) at the repo root.
**Decision:** keep `Data/` as the canonical raw location and treat it as read-only. `data/` is reserved for derived outputs (`interim/`, `processed/`). All code resolves paths from a single project-root constant, so this is a one-line change if we ever migrate.

---

## 6. Implementation phases

### Phase 1 — Data Understanding
**Objective:** understand structure, relationships, data quality.

Tasks: load all 9 CSVs · inspect dimensions, dtypes, missing values · find duplicates · identify PK/FK · map relationships · document the grain of each table · build a data dictionary · investigate data-quality issues.

**Deliverables:** data dictionary · relationship documentation · initial data-quality report.

### Phase 2 — Exploratory Data Analysis
**Objective:** find business patterns and actionable insight.

Tasks: univariate analysis · missing-value analysis · outliers · correlation · revenue and order-volume trends · monthly/seasonal patterns · performance by category · performance by region · delivery duration · late-delivery analysis · review analysis · seller performance.

**Deliverables:** EDA notebook · visualizations · **≥5 documented business findings**.

### Phase 3 — Data Cleaning and Preprocessing
**Objective:** a reproducible preprocessing pipeline.

Tasks: handle missing values by business meaning · handle duplicates · parse timestamps · standardize names and dtypes · validate PK/FK · investigate extreme values · aggregate one-to-many tables to the correct grain · build the order-level analytical dataset · reusable functions · Pandera validation · unit tests.

> **Critical:** payments and order items must be aggregated **separately** before joining to orders. The final ML dataset must have **exactly one row per order**.

**Deliverables:** reusable preprocessing modules · clean analytical dataset · data-validation tests.

### Phase 4 — Data Engineering and ETL
**Objective:** automated ingestion and transformation.

**GCS:** create bucket · upload raw CSVs · organize into logical directories · preserve raw data · store ingestion metadata.

**BigQuery datasets:** `raw` (as ingested) · `staging` (cleaned/standardized) · `analytics` (business-ready). Load via Python + Google Cloud SDK.

**dbt models**
- Staging: `stg_orders`, `stg_customers`, `stg_products`, `stg_sellers`, `stg_payments`, `stg_order_items`, `stg_reviews`
- Intermediate: `int_order_items_aggregated`, `int_payments_aggregated`, `int_order_delivery`
- Marts: `fact_orders`, `fact_order_items`, `dim_customers`, `dim_products`, `dim_sellers`, `dim_date`

**dbt tests:** unique · not-null · referential integrity · accepted values · custom business-logic tests.

**Airflow DAG:** extract → validate input files → upload to GCS → load to BigQuery → run dbt → run data-quality tests → publish analytics tables. Include dependencies, retries, logging, idempotency. Run locally via Docker Compose first.

**Deliverables:** automated ETL pipeline · GCS integration · BigQuery warehouse · dbt models and tests · Airflow DAG.

### Phase 5 — Business Intelligence
**Objective:** Power BI dashboards connected to BigQuery.

**Dashboard 1 — Executive Overview:** total revenue · total orders · AOV · monthly revenue · revenue growth · revenue by category · revenue by region.

**Dashboard 2 — Logistics Performance:** average delivery duration · late-delivery % · performance by seller · performance by region · freight costs · delay trends.

**Dashboard 3 — Customer Intelligence:** repeat-purchase rate · customer distribution · average review score · score distribution · customer lifetime revenue · purchasing behavior.

Also: DAX measures · date relationships · Power Query · interactive filters/slicers · Excel report with PivotTables.

**Deliverables:** three-page Power BI dashboard · Excel analytical report.

### Phase 6 — Feature Engineering and Machine Learning
**Objective:** a reproducible delivery-delay model.

Tasks: define the target · filter eligible historical orders · identify features available at placement · build numerical and categorical features · engineer geographic distance · compute leakage-safe historical seller stats · scikit-learn preprocessing pipelines · chronological splits · Dummy + Logistic baselines · RF/LightGBM/XGBoost · temporal CV · Optuna · threshold optimization · classification metrics · SHAP · MLflow tracking · register the best model.

**Deliverables:** reproducible training pipeline · evaluation report · SHAP analysis · MLflow experiment history and registered model.

### Phase 7 — Model Deployment
**Objective:** an accessible prediction service.

**FastAPI:** `GET /health` · `POST /predict` · `GET /model/info` · `GET /metrics`.
`/predict` returns: delay probability · binary prediction · risk classification · model version.

**Streamlit:** enter order info · generate predictions · view probabilities · inspect SHAP explanations · view model info · inspect monitoring reports.

**Docker:** containerize FastAPI, Streamlit, MLflow and supporting services; Docker Compose for local dev.

**GCP:** build image → push to Artifact Registry → deploy to Cloud Run → configure env vars and permissions → connect the app to the API → configure logging and health checks. Streamlit may stay local as a portfolio demo.

**Deliverables:** Dockerized application · deployed prediction API · interactive prediction interface.

### Phase 8 — Model and Data Monitoring
**Objective:** monitor inputs, predictions and performance post-deployment.

**Evidently:** feature distribution drift · prediction drift · missing values · invalid feature values · categorical distribution changes.

**Performance (when labels arrive):** precision · recall · F1 · PR-AUC · confusion matrix · calibration.

**Retraining workflow:** receive new batch → validate → generate features → check drift → evaluate when labels arrive → trigger candidate retraining → compare candidate vs production → register if it passes.

> The Olist dataset is historical and static. Use **simulated incoming batches and delayed labels**, and clearly label simulated monitoring as simulated.

**Deliverables:** Evidently reports · monitoring pipeline · retraining workflow · model version comparison.

### Phase 9 — CI/CD and Documentation
**Objective:** reproducible, maintainable, professionally documented.

**GitHub Actions:** linting · unit tests · integration tests · dbt tests · Docker image build · deployment.

**README must include:** project overview · business problems · dataset description and attribution · architecture diagram · tech stack · repository structure · data pipeline explanation · EDA findings · Power BI screenshots · ML methodology · evaluation results · SHAP visualizations · API documentation · deployment instructions · monitoring methodology · limitations and future improvements.

---

## 7. Final deliverables checklist

| Category | Deliverable |
|----------|-------------|
| Data analysis | EDA report and business findings |
| Data engineering | Automated ETL pipeline |
| Cloud | GCS and BigQuery integration |
| Data modeling | dbt transformations and tests |
| Orchestration | Airflow DAG |
| Business intelligence | Power BI dashboards |
| Machine learning | Delivery-delay prediction |
| Explainability | SHAP analysis |
| MLOps | MLflow tracking and registry |
| Monitoring | Evidently reports |
| Backend | FastAPI prediction service |
| Frontend | Streamlit application |
| Infrastructure | Docker |
| CI/CD | GitHub Actions |
| Documentation | Complete README and architecture diagram |

---

## 8. Development principles

- Prioritize correctness and reproducibility over the number of technologies used.
- Use modular, reusable Python code.
- Keep notebooks for exploration and experiments, not production logic.
- Use SQL and dbt for warehouse transformations.
- Implement automated data-quality checks.
- Prevent data leakage in all ML stages.
- Use chronological validation for the delivery prediction task.
- Track model experiments and dataset versions.
- Keep secrets and credentials outside Git.
- Avoid unnecessary cloud spending.
- Build and test locally before deploying to GCP.
- Document architectural decisions and business assumptions.

**Final objective:** a complete, production-oriented E-commerce Intelligence Platform demonstrating practical Data Science, Data Engineering, Business Intelligence, Cloud Computing and MLOps skills.

---

## 9. How this file relates to the agent customizations

| File | Role |
|------|------|
| `CONTEXT.md` (this file) | The **plan**: scope, stack, structure, phases, ML spec |
| `.github/instructions/olist-platform.instructions.md` | The **rules**: workspace facts, non-negotiable guardrails, conventions |
| `.github/instructions/olist-ml-leakage.instructions.md` | The **leakage contract** for the ML work |
| `.github/agents/*.agent.md` | Role-specific specialists (data engineer, EDA, ML, MLOps, reviewer, debugger) |
| `.github/prompts/*.prompt.md` | Slash commands: `/olist-phase-status`, `/olist-new-notebook`, `/olist-add-dbt-model`, `/olist-leakage-check`, `/olist-commit-check` |

If the plan and the instructions ever disagree, **this file wins on scope** and the instructions win on **guardrails** — then fix whichever is stale.

---

## 10. Phase 1 findings (verified against the data)

All numbers below were computed in `notebooks/01_data_understanding.ipynb`.

### Table inventory

| Table | Rows | Cols | Grain | Primary key |
|-------|------|------|-------|-------------|
| `geolocation` | 1,000,163 | 5 | many rows / ZIP prefix | none (19,015 prefixes, mean 52.6 rows) |
| `order_items` | 112,650 | 7 | 1 row / line item | `(order_id, order_item_id)` |
| `order_payments` | 103,886 | 5 | 1 row / payment line | `(order_id, payment_sequential)` |
| `orders` | 99,441 | 8 | 1 row / order | `order_id` |
| `customers` | 99,441 | 5 | 1 row / order-scoped customer | `customer_id` |
| `order_reviews` | 99,224 | 7 | 1 row / review | **none** — 547 orders have >1 review |
| `products` | 32,951 | 9 | 1 row / product | `product_id` |
| `sellers` | 3,095 | 4 | 1 row / seller | `seller_id` |
| `category_translation` | 71 | 2 | 1 row / category | `product_category_name` |

`customer_id` is **order-scoped** (1:1 with `order_id`); `customer_unique_id` is person-scoped — 96,096 people, 2,997 repeat customers (max 17 orders).

### FK integrity

All six transactional FKs resolve with **zero orphans**. Soft lookups have gaps:

| Relationship | Orphans |
|---|---|
| `customers.zip → geolocation` | 278 (0.28%) |
| `sellers.zip → geolocation` | 7 (0.23%) |
| `products.category → translation` | 13 rows / 2 categories (`pc_gamer`, `portateis_cozinha_e_preparadores_de_alimentos`) |

Coverage gaps requiring `LEFT JOIN`: 775 orders without items, 1 without payment, 768 without review.

### ML label eligibility

**96,470 of 99,441 orders (97.01%)** are eligible: `status == 'delivered'` AND both delivery dates present.
Positives **7,826 (8.11%)**, negatives 88,644 — imbalance **1 : 11.33**.
Delivery duration median 10 days (max 209); estimated lead time median 23 days.

### Data-quality issues and how Phase 3 will handle them

| # | Issue | Evidence | Phase 3 decision |
|---|-------|----------|------------------|
| 1 | `order_estimated_delivery_date` is **date-only** (100% at `00:00:00`) | 1,292 orders delivered *on* the estimate day are labelled late — 16.5% of all positives. End-of-day reading drops the late rate 8.11% → 6.77% | **Decide and document explicitly.** Default: compare against end-of-day (`23:59:59`) as the customer-facing promise. Record the choice in the README; it changes the base rate materially |
| 2 | Reviews are not 1:1 with orders | 547 orders with >1 review; 814 duplicate `review_id` rows | Aggregate reviews to order grain (latest review, or mean score) **before** joining |
| 3 | `geolocation` is a sample, not a lookup | 93.51% of prefixes hold >1 distinct coordinate; only 1,234 unambiguous; 8 prefixes span >1 state; lat/lng extends outside Brazil | Aggregate to one coordinate per ZIP prefix (median lat/lng) and document the approximation |
| 4 | Missing delivery dates | 2,965 nulls, incl. **8 orders marked `delivered` with no delivery timestamp** and 6 `canceled` orders that have one | Exclude from training labels; keep in the analytical dataset with a flag |
| 5 | Unusable temporal tails | 329 orders in 2016 (0.33%); only 20 in Sep–Oct 2018 vs ~6,300/month | Chronological splits must run **2017-01 → 2018-08** |

Also documented: 166 carrier-before-purchase and 23 delivered-before-carrier violations (purchase→approved and purchase→delivered are clean, so the prediction timestamp is trustworthy); 610 products (1.85%) with the entire metadata block null; 383 zero-freight lines; 9 zero-value payment lines; 40,977 reviews with text vs 58,247 without (mean score 3.67 vs 4.38).
