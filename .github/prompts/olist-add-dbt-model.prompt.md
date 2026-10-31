---
description: "Scaffold a dbt model (staging, intermediate or mart) for the Olist warehouse, with its schema.yml tests and correct grain handling."
name: "Olist Add dbt Model"
agent: "Olist Data Engineer"
tools: [read, edit, search, execute]
argument-hint: "Model name and layer, e.g. 'int_payments_aggregated'"
---
Add a dbt model to the Olist warehouse.

1. Ask (or infer from the argument) the **layer** and **model name**:
   - `staging` → `stg_<source_table>`: rename to snake_case, cast types, trim strings, no joins, no business logic.
   - `intermediate` → `int_<purpose>`: joins and aggregations. **Aggregate one-to-many tables separately** — never join `order_items` and `payments` to `orders` in the same step.
   - `marts` → `fact_<entity>` or `dim_<entity>`: business-ready, documented columns.
2. Create `dbt/models/<layer>/<model_name>.sql` with:
   - A header comment stating the **grain** and the **primary key**.
   - CTEs named `source` → `renamed` → `final`.
   - Explicit column list (no `select *` in marts).
3. Add/update `dbt/models/<layer>/schema.yml`:
   - `unique` + `not_null` on the primary key.
   - `relationships` tests for every foreign key.
   - `accepted_values` for low-cardinality categoricals (e.g. `order_status`).
   - Column `description` for every column in marts.
4. If the model encodes a business rule (e.g. late-delivery definition), add a singular test in `dbt/tests/`.
5. Run `dbt build --select <model_name>+` and report the real output.

Report: file paths, grain, key, tests added, and the build result.
