# Power BI measure catalogue

Every measure below is written against the `analytics` star schema in BigQuery. The expected
value column is the figure the measure must return on the full dataset — use it to verify the
model after connecting, before building any visual.

## Model setup

Import these six tables from the `analytics` dataset:

| Table | Grain | Rows |
|-------|-------|------|
| `fact_orders` | one row per order | 99,441 |
| `fact_order_items` | one row per line item | 112,650 |
| `dim_customers` | one row per `customer_id` | 99,441 |
| `dim_products` | one row per `product_id` | 32,951 |
| `dim_sellers` | one row per `seller_id` | 3,095 |
| `dim_date` | one row per day | 1,096 |

Relationships (all single-direction, `dim` → `fact`):

```
dim_date[date_day]        → fact_orders[order_purchase_timestamp]   (date, not timestamp)
dim_customers[customer_id] → fact_orders[customer_id]
dim_sellers[seller_id]     → fact_orders[primary_seller_id]
dim_products[product_id]   → fact_orders[primary_product_id]
```

`fact_order_items` is a **second fact table at a different grain**. Do not relate it to
`fact_orders` — the join multiplies rows. Relate it to the dimensions only, and use it solely
for category-level revenue.

### Two traps that will produce wrong numbers

**1. `is_label_eligible`.** Only 96,470 of 99,441 orders have a known delivery outcome. Every
delivery metric must filter on it. Without the filter the late rate reads 7.87% instead of
8.11%, and average delivery duration is understated.

**2. `customer_unique_id`.** `customer_id` is order-scoped — one per order. Repeat-purchase
analysis must group by `customer_unique_id` (96,096 people). Grouping by `customer_id` returns
a 0% repeat rate, which looks plausible and is meaningless.

---

## Base measures

```dax
Total Orders =
COUNTROWS ( fact_orders )
```
**Expected: 99,441**

```dax
Eligible Orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_label_eligible] = TRUE )
```
**Expected: 96,470**

```dax
Total Revenue =
SUM ( fact_orders[item_revenue] )
```
**Expected: 13,591,643.70**

```dax
Total Freight =
SUM ( fact_orders[freight_value] )
```
**Expected: 2,251,909.54**

```dax
Average Order Value =
DIVIDE ( [Total Revenue], [Total Orders] )
```
**Expected: 137.75**

```dax
Freight Share of Revenue =
DIVIDE ( [Total Freight], [Total Revenue] )
```
**Expected: 16.57%**

---

## Delivery measures

```dax
Late Orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_late] = TRUE )
```
**Expected: 7,826**

```dax
Late Rate % =
DIVIDE ( [Late Orders], [Eligible Orders] )
```
**Expected: 8.11%**

```dax
Average Delivery Days =
CALCULATE (
    AVERAGE ( fact_orders[delivery_days] ),
    fact_orders[is_label_eligible] = TRUE
)
```
**Expected: 12.50**

```dax
Average Estimated Lead Days =
AVERAGE ( fact_orders[estimated_lead_days] )
```
**Expected: 24.4**

```dax
Average Delay Days (late only) =
CALCULATE (
    AVERAGE ( fact_orders[delay_days] ),
    fact_orders[is_late] = TRUE
)
```
**Expected: 8.9**

```dax
On Time Rate % =
1 - [Late Rate %]
```
**Expected: 91.89%**

---

## Customer measures

```dax
Unique Customers =
DISTINCTCOUNT ( fact_orders[customer_unique_id] )
```
**Expected: 96,096**

```dax
Orders per Customer =
DIVIDE ( [Total Orders], [Unique Customers] )
```
**Expected: 1.035**

```dax
Repeat Customers =
COUNTROWS (
    FILTER (
        VALUES ( fact_orders[customer_unique_id] ),
        CALCULATE ( COUNTROWS ( fact_orders ) ) > 1
    )
)
```
**Expected: 2,997**

```dax
Repeat Purchase Rate % =
DIVIDE ( [Repeat Customers], [Unique Customers] )
```
**Expected: 3.12%**

```dax
Revenue per Customer =
DIVIDE ( [Total Revenue], [Unique Customers] )
```
**Expected: 141.44**

```dax
Average Review Score =
AVERAGE ( fact_orders[review_score] )
```
**Expected: 4.087**

```dax
Low Score Orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[review_score] <= 2 )
```
**Expected: 14,476**

```dax
Low Score Rate % =
DIVIDE ( [Low Score Orders], COUNTROWS ( fact_orders ) )
```
**Expected: 14.56%**

---

## Time intelligence

`dim_date` must be marked as the date table (`Table tools → Mark as date table`), and
`fact_orders[order_purchase_timestamp]` must be related to `dim_date[date_day]` on the **date**
part, not the timestamp.

```dax
Revenue MoM % =
VAR Current = [Total Revenue]
VAR Prior =
    CALCULATE ( [Total Revenue], DATEADD ( dim_date[date_day], -1, MONTH ) )
RETURN
    DIVIDE ( Current - Prior, Prior )
```

```dax
Revenue YoY % =
VAR Current = [Total Revenue]
VAR Prior =
    CALCULATE ( [Total Revenue], SAMEPERIODLASTYEAR ( dim_date[date_day] ) )
RETURN
    DIVIDE ( Current - Prior, Prior )
```

```dax
Revenue 3M Rolling =
CALCULATE (
    [Total Revenue],
    DATESINPERIOD ( dim_date[date_day], MAX ( dim_date[date_day] ), -3, MONTH )
)
```

```dax
Late Rate 3M Rolling =
CALCULATE (
    [Late Rate %],
    DATESINPERIOD ( dim_date[date_day], MAX ( dim_date[date_day] ), -3, MONTH )
)
```

> **Note on the temporal tails.** 329 orders fall in 2016 (0.33%) and only 20 in Sep–Oct 2018
> against ~6,300 per normal month. Any trend visual should be filtered to
> **2017-01-01 → 2018-08-31**, or the first and last points will mislead. Add a slicer on
> `dim_date[date_day]` and set that as the default range.

---

## Category measures

Category revenue must come from `fact_order_items`, not `fact_orders` — an order with items
from two categories has only one `product_category` in `fact_orders` (the primary item's).

```dax
Category Revenue =
SUM ( fact_order_items[price] )
```

```dax
Category Revenue Share % =
DIVIDE (
    [Category Revenue],
    CALCULATE ( [Category Revenue], ALL ( dim_products[product_category] ) )
)
```

```dax
Category Late Rate % =
CALCULATE (
    DIVIDE (
        CALCULATE ( COUNTROWS ( fact_order_items ), fact_order_items[is_late] = TRUE ),
        CALCULATE ( COUNTROWS ( fact_order_items ), fact_order_items[is_label_eligible] = TRUE )
    )
)
```

---

## Verification checklist

After connecting, put each measure in a card visual and compare against the expected value.
If any figure is off, check in this order:

1. Is `is_label_eligible` filtered on the delivery measures?
2. Is the `dim_date` relationship on the date part, not the timestamp?
3. Is `fact_order_items` related to `fact_orders` anywhere? (It must not be.)
4. Is the repeat-purchase measure grouping by `customer_unique_id`?
5. Is the date slicer excluding 2016 and Sep–Oct 2018?

A model that reproduces all 20 expected values is correct. That is the acceptance test for
this phase.
