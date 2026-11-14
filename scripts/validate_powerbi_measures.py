"""Validate the Power BI measure catalogue against the BigQuery analytics star schema.

Every measure in ``docs/powerbi-measures.md`` carries an expected value. This script runs the
equivalent SQL and compares, so the semantic model can be checked before it is ever opened in
Power BI Desktop. A model that reproduces all of these is correct.

Usage:
    python scripts/validate_powerbi_measures.py
"""

from __future__ import annotations

import sys

from google.cloud import bigquery

PROJECT = "olist-intelligence"
DATASET = "analytics"

FACT_ORDERS = f"`{PROJECT}.{DATASET}.fact_orders`"
FACT_ITEMS = f"`{PROJECT}.{DATASET}.fact_order_items`"

#: (measure name, expected value, tolerance) — expected values come from the measure catalogue.
EXPECTED: list[tuple[str, float, float]] = [
    ("Total Orders", 99_441, 0),
    ("Eligible Orders", 96_470, 0),
    ("Total Revenue", 13_591_643.70, 0.01),
    ("Total Freight", 2_251_909.54, 0.01),
    ("Average Order Value", 136.68, 0.01),
    ("Freight Share of Revenue %", 16.57, 0.01),
    ("Late Orders", 7_826, 0),
    ("Late Rate %", 8.11, 0.01),
    ("Average Delivery Days", 12.50, 0.01),
    ("Average Estimated Lead Days", 24.4, 0.1),
    ("Average Delay Days (late only)", 8.9, 0.1),
    ("On Time Rate %", 91.89, 0.01),
    ("Unique Customers", 96_096, 0),
    ("Orders per Customer", 1.035, 0.001),
    ("Repeat Customers", 2_997, 0),
    ("Repeat Purchase Rate %", 3.12, 0.01),
    ("Revenue per Customer", 141.44, 0.01),
    ("Average Review Score", 4.087, 0.001),
    ("Low Score Orders", 14_476, 0),
    ("Low Score Rate %", 14.56, 0.01),
]

QUERY = f"""
WITH base AS (
    SELECT
        COUNT(*)                                        AS total_orders,
        COUNTIF(is_label_eligible)                      AS eligible_orders,
        SUM(item_revenue)                               AS total_revenue,
        SUM(freight_value)                              AS total_freight,
        COUNTIF(is_late)                                AS late_orders,
        AVG(IF(is_label_eligible, delivery_days, NULL)) AS avg_delivery_days,
        AVG(estimated_lead_days)                        AS avg_est_lead_days,
        AVG(IF(is_late, delay_days, NULL))              AS avg_delay_days_late,
        COUNT(DISTINCT customer_unique_id)              AS unique_customers,
        AVG(review_score)                               AS avg_review_score,
        COUNTIF(review_score <= 2)                      AS low_score_orders
    FROM {FACT_ORDERS}
),
repeat AS (
    SELECT COUNT(*) AS repeat_customers
    FROM (
        SELECT customer_unique_id
        FROM {FACT_ORDERS}
        GROUP BY customer_unique_id
        HAVING COUNT(*) > 1
    )
)
SELECT
    b.total_orders,
    b.eligible_orders,
    b.total_revenue,
    b.total_freight,
    SAFE_DIVIDE(b.total_revenue, b.total_orders)                AS aov,
    SAFE_DIVIDE(b.total_freight, b.total_revenue) * 100         AS freight_share_pct,
    b.late_orders,
    SAFE_DIVIDE(b.late_orders, b.eligible_orders) * 100         AS late_rate_pct,
    b.avg_delivery_days,
    b.avg_est_lead_days,
    b.avg_delay_days_late,
    (1 - SAFE_DIVIDE(b.late_orders, b.eligible_orders)) * 100   AS on_time_rate_pct,
    b.unique_customers,
    SAFE_DIVIDE(b.total_orders, b.unique_customers)             AS orders_per_customer,
    r.repeat_customers,
    SAFE_DIVIDE(r.repeat_customers, b.unique_customers) * 100   AS repeat_rate_pct,
    SAFE_DIVIDE(b.total_revenue, b.unique_customers)            AS revenue_per_customer,
    b.avg_review_score,
    b.low_score_orders,
    SAFE_DIVIDE(b.low_score_orders, b.total_orders) * 100       AS low_score_rate_pct
FROM base b CROSS JOIN repeat r
"""

#: Map measure name -> result column.
COLUMNS = {
    "Total Orders": "total_orders",
    "Eligible Orders": "eligible_orders",
    "Total Revenue": "total_revenue",
    "Total Freight": "total_freight",
    "Average Order Value": "aov",
    "Freight Share of Revenue %": "freight_share_pct",
    "Late Orders": "late_orders",
    "Late Rate %": "late_rate_pct",
    "Average Delivery Days": "avg_delivery_days",
    "Average Estimated Lead Days": "avg_est_lead_days",
    "Average Delay Days (late only)": "avg_delay_days_late",
    "On Time Rate %": "on_time_rate_pct",
    "Unique Customers": "unique_customers",
    "Orders per Customer": "orders_per_customer",
    "Repeat Customers": "repeat_customers",
    "Repeat Purchase Rate %": "repeat_rate_pct",
    "Revenue per Customer": "revenue_per_customer",
    "Average Review Score": "avg_review_score",
    "Low Score Orders": "low_score_orders",
    "Low Score Rate %": "low_score_rate_pct",
}


def main() -> int:
    client = bigquery.Client(project=PROJECT)
    row = next(iter(client.query(QUERY).result()))

    failures = 0
    print(f"{'Measure':34s} {'Expected':>14s} {'Actual':>14s}  Status")
    print("-" * 78)
    for name, expected, tol in EXPECTED:
        actual = row[COLUMNS[name]]
        actual_f = float(actual) if actual is not None else float("nan")
        ok = abs(actual_f - expected) <= tol
        if not ok:
            failures += 1
        print(
            f"{name:34s} {expected:>14,.3f} {actual_f:>14,.3f}  "
            f"{'OK' if ok else 'MISMATCH'}"
        )

    print("-" * 78)
    if failures:
        print(f"{failures} of {len(EXPECTED)} measures do not match the catalogue.")
        return 1
    print(f"All {len(EXPECTED)} measures match the catalogue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
