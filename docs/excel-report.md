# Excel analytical report

A single workbook that mirrors the three dashboards in a form a non-Power-BI reader can open.
Built with Power Query against the same BigQuery tables, so the numbers reconcile with the
dashboards by construction rather than by coincidence.

## Workbook structure

| Sheet | Purpose |
|-------|---------|
| `README` | What the workbook contains, the data range, and the two caveats |
| `Executive` | Revenue, orders, AOV, monthly trend, category and state breakdown |
| `Logistics` | Late rate, delivery duration, seller and state performance |
| `Customer` | Repeat purchase, review distribution, satisfaction by category |
| `Data` | The Power Query output — one flat table, refreshed from BigQuery |

## Power Query connections

Four queries, each a direct BigQuery table load. No custom SQL — the warehouse already holds
the right grain, and hand-written SQL in Power Query is a second place for logic to drift.

| Query | Source table | Rows |
|-------|--------------|------|
| `qFactOrders` | `analytics.fact_orders` | 99,441 |
| `qFactOrderItems` | `analytics.fact_order_items` | 112,650 |
| `qDimCustomers` | `analytics.dim_customers` | 99,441 |
| `qDimProducts` | `analytics.dim_products` | 32,951 |
| `qDimSellers` | `analytics.dim_sellers` | 3,095 |
| `qDimDate` | `analytics.dim_date` | 1,096 |

Load `qFactOrders` to the `Data` sheet; keep the dimensions as **connection only** so the
workbook stays small.

### Transformations to apply in Power Query

On `qFactOrders`:

1. Set `order_purchase_timestamp` to **Date** type (not Date/Time) so it joins to `qDimDate`.
2. Add a custom column `YearMonth` = `Date.ToText([order_purchase_timestamp], "yyyy-MM")`.
3. Add a custom column `IsLateLabel` = `if [is_late] = true then "Late" else "On time"`.
4. Filter out rows where `is_label_eligible` is false **only on the Logistics sheet's query
   copy** — keep the full set in `Data` so the Executive figures stay complete.

> Duplicating the query for the filtered version is deliberate. Filtering at the source would
> silently change the Executive totals, and the two pages would stop reconciling.

## Sheet: Executive

**KPI block** (cells B2:E2, one card each)

| Metric | Value | Formula |
|--------|-------|---------|
| Total Revenue | 13,591,643.70 | `=SUM(Data[item_revenue])` |
| Total Orders | 99,441 | `=COUNTA(Data[order_id])` |
| Average Order Value | 137.75 | `=B3/B4` |
| Freight Share | 16.57% | `=SUM(Data[freight_value])/B3` |

**Monthly revenue** — PivotTable
- Rows: `YearMonth`
- Values: Sum of `item_revenue`
- Filter: `YearMonth` between `2017-01` and `2018-08`
- Chart: line, with a 3-month moving average as a second series

**Revenue by category** — PivotTable from `qFactOrderItems`
- Rows: `product_category`
- Values: Sum of `price`
- Sort descending, top 15
- Chart: horizontal bar

**Revenue by state** — PivotTable
- Rows: `customer_state`
- Values: Sum of `item_revenue`
- Chart: bar, sorted descending

## Sheet: Logistics

**KPI block**

| Metric | Value | Formula |
|--------|-------|---------|
| Late Rate | 8.11% | `=COUNTIFS(eligible_range,TRUE,late_range,TRUE)/COUNTIF(eligible_range,TRUE)` |
| Late Orders | 7,826 | `=COUNTIFS(eligible_range,TRUE,late_range,TRUE)` |
| Avg Delivery Days | 12.50 | `=AVERAGEIFS(delivery_range,eligible_range,TRUE)` |
| Avg Delay (late) | 8.9 | `=AVERAGEIFS(delay_range,late_range,TRUE)` |

**Late rate by month** — PivotTable
- Rows: `YearMonth`
- Values: Average of `is_late` (formatted as %)
- Filter: `is_label_eligible` = TRUE
- Chart: line

**Late rate by state** — PivotTable
- Rows: `customer_state`
- Values: Average of `is_late`, Count of `order_id`
- Filter: count ≥ 50
- Conditional formatting: 3-colour scale on the rate column

**Seller performance** — PivotTable
- Rows: `primary_seller_id`
- Values: Count of `order_id`, Average of `is_late`, Average of `delivery_days`
- Filter: count ≥ 50, top 20 by count

## Sheet: Customer

**KPI block**

| Metric | Value | Formula |
|--------|-------|---------|
| Unique Customers | 96,096 | `=SUMPRODUCT(1/COUNTIF(Data[customer_unique_id],Data[customer_unique_id]))` |
| Repeat Purchase Rate | 3.12% | see note below |
| Avg Review Score | 4.087 | `=AVERAGE(Data[review_score])` |
| Low Score Rate | 14.56% | `=COUNTIF(Data[review_score],"<=2")/COUNTA(Data[order_id])` |

> **Repeat purchase rate needs a helper column.** `SUMPRODUCT(1/COUNTIF(...))` counts distinct
> values but cannot count *how many orders each customer placed*. Add a helper column
> `OrdersPerCustomer` on a separate sheet:
> `=COUNTIF(Data[customer_unique_id], [@customer_unique_id])`, then
> `=COUNTIF(helper_range, ">1") / COUNTA(helper_range)`.
> Grouping by `customer_id` instead returns 0% — it is order-scoped, one per order.

**Review score distribution** — PivotTable
- Rows: `review_score`
- Values: Count of `order_id`
- Chart: column

**Late vs on-time by score** — PivotTable
- Rows: `review_score`
- Columns: `IsLateLabel`
- Values: Count of `order_id`
- Chart: clustered column
- *Expected:* mean score 2.57 for late orders against 4.29 for on-time.

**Orders per customer** — PivotTable on the helper column
- Rows: `OrdersPerCustomer`
- Values: Count
- *Expected:* 93,099 customers with one order, 2,997 with more than one, maximum 17.

## Sheet: README

State four things:

1. **Data range.** Orders span 2016-09-04 to 2018-10-17. Trend charts are filtered to
   2017-01 → 2018-08 because 2016 holds 329 orders and Sep–Oct 2018 holds 20, against ~6,300
   in a normal month.
2. **Label eligibility.** 96,470 of 99,441 orders have a known delivery outcome. Delivery
   metrics exclude the other 2,971; revenue metrics include them.
3. **Customer identity.** `customer_id` is order-scoped. Repeat-purchase analysis uses
   `customer_unique_id` (96,096 people).
4. **Refresh.** Data → Refresh All re-runs the Power Query loads against BigQuery. Requires
   the same Google account used for the Power BI connection.

## Acceptance test

Every figure in the KPI blocks must match the corresponding measure in the Power BI catalogue.
If the workbook and the dashboard disagree, one of them has a filter the other lacks — find it
before publishing either.
