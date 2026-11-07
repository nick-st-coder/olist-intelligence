# Power BI dashboard specifications

Three pages, each answering a distinct set of the business questions in `CONTEXT.md` §2. Every
visual below is specified with its table, field, aggregation and the figure it should show on
the full dataset, so the page can be checked against the warehouse rather than eyeballed.

All measures referenced here are defined in [`powerbi-measures.md`](powerbi-measures.md).

---

## Page 1 — Executive Overview

**Business questions answered:** how does revenue change over time; which categories and
regions drive it; what is the average order value; how do freight costs affect performance.

### KPI cards (top row)

| Card | Measure | Expected |
|------|---------|----------|
| Total Revenue | `[Total Revenue]` | 13,591,643.70 |
| Total Orders | `[Total Orders]` | 99,441 |
| Average Order Value | `[Average Order Value]` | 137.75 |
| Freight Share | `[Freight Share of Revenue]` | 16.57% |

### Visuals

**Monthly revenue trend** — line chart
- Axis: `dim_date[year_month]`
- Values: `[Total Revenue]`, `[Revenue 3M Rolling]`
- Filter: `dim_date[date_day]` between 2017-01-01 and 2018-08-31
- *Why the filter:* 2016 holds 329 orders and Sep–Oct 2018 holds 20, against ~6,300 in a normal
  month. Unfiltered, the line starts and ends with a misleading collapse.

**Revenue by category** — horizontal bar chart, top 15
- Axis: `dim_products[product_category]`
- Values: `[Category Revenue]` (from `fact_order_items`)
- Sort: descending
- *Why `fact_order_items`:* `fact_orders` carries only the primary item's category, so an order
  spanning two categories would be attributed entirely to one.

**Revenue by state** — filled map or bar chart
- Axis: `dim_customers[customer_state]`
- Values: `[Total Revenue]`
- *Expected shape:* SP dominates, followed by RJ and MG. The three account for roughly 60% of
  revenue.

**Revenue vs freight** — scatter or dual-axis line
- Axis: `dim_date[year_month]`
- Values: `[Total Revenue]`, `[Total Freight]`
- *Reading:* freight tracks revenue closely but is not proportional — the ratio is the story.

### Slicers
`dim_date[year]`, `dim_customers[customer_state]`, `dim_products[product_category]`.

---

## Page 2 — Logistics Performance

**Business questions answered:** what share of orders arrive late; which sellers and regions
are slowest; does distance matter; which categories are slowest.

> **Every measure on this page must filter `is_label_eligible = TRUE`.** 2,971 orders have no
> known delivery outcome. Including them understates the late rate to 7.87% and drags average
> delivery duration down.

### KPI cards (top row)

| Card | Measure | Expected |
|------|---------|----------|
| Late Rate | `[Late Rate %]` | 8.11% |
| Late Orders | `[Late Orders]` | 7,826 |
| Avg Delivery Days | `[Average Delivery Days]` | 12.50 |
| Avg Delay (late only) | `[Average Delay Days (late only)]` | 8.9 |

### Visuals

**Late rate trend** — line chart
- Axis: `dim_date[year_month]`
- Values: `[Late Rate %]`, `[Late Rate 3M Rolling]`
- Filter: 2017-01-01 → 2018-08-31
- *Reading:* the late rate is not flat — it spikes in specific months, which is the most
  actionable finding on this page.

**Late rate by state** — bar chart, sorted descending
- Axis: `dim_customers[customer_state]`
- Values: `[Late Rate %]`, `[Eligible Orders]` (as tooltip)
- *Reading:* the worst states are the northern and north-eastern ones, which are also the
  furthest from the São Paulo seller base.

**Seller performance** — table, top 20 by volume
- Columns: `dim_sellers[seller_id]`, `[Eligible Orders]`, `[Late Rate %]`,
  `[Average Delivery Days]`
- Filter: `[Eligible Orders] >= 50` — below that the rate is noise
- Conditional formatting: colour scale on `[Late Rate %]`

**Distance vs delivery time** — scatter
- X: `fact_orders[seller_customer_distance_km]` (binned)
- Y: `[Average Delivery Days]`
- *Note:* the distance column is null for 775 orders with no line items. The scatter will show
  a null bucket; exclude it rather than letting it read as zero.

**Freight cost by state** — bar chart
- Axis: `dim_customers[customer_state]`
- Values: `[Total Freight]`, `[Average Order Value]`

### Slicers
`dim_date[year_month]`, `dim_customers[customer_state]`, `dim_sellers[seller_state]`.

---

## Page 3 — Customer Intelligence

**Business questions answered:** does late delivery depress review scores; which categories and
regions are least satisfied; what share of customers buy again; who generates the most revenue.

> **Repeat-purchase measures must group by `customer_unique_id`, not `customer_id`.**
> `customer_id` is order-scoped — one per order — so grouping by it returns a 0% repeat rate
> that looks plausible and is meaningless.

### KPI cards (top row)

| Card | Measure | Expected |
|------|---------|----------|
| Unique Customers | `[Unique Customers]` | 96,096 |
| Repeat Purchase Rate | `[Repeat Purchase Rate %]` | 3.12% |
| Avg Review Score | `[Average Review Score]` | 4.087 |
| Low Score Rate | `[Low Score Rate %]` | 14.56% |

### Visuals

**Review score distribution** — column chart
- Axis: `fact_orders[review_score]` (1–5)
- Values: `[Total Orders]`
- *Expected shape:* heavily skewed to 5 (57.3%), with a secondary bump at 1 (11.4%).
  The bimodality is the finding — customers are either delighted or furious.

**Late delivery vs review score** — clustered column
- Axis: `fact_orders[review_score]`
- Values: `[Total Orders]`, split by `fact_orders[is_late]`
- *Reading:* this is the single most important chart on the page. Late orders concentrate at
  scores 1–2; on-time orders concentrate at 4–5. The mean score is **2.57 for late orders
  against 4.29 for on-time** — a gap of 1.73 points.

**Repeat purchase distribution** — column chart
- Axis: order count per `customer_unique_id` (1, 2, 3, …)
- Values: `[Unique Customers]`
- *Expected:* 93,099 customers with one order, 2,997 with more than one, maximum 17.

**Customer revenue concentration** — Pareto / cumulative line
- Axis: `dim_customers[customer_unique_id]` ranked by `[Total Revenue]`
- Values: cumulative `[Revenue per Customer]`
- *Reading:* revenue is highly concentrated — the top decile of customers accounts for a
  disproportionate share.

**Satisfaction by category** — bar chart
- Axis: `dim_products[product_category]`
- Values: `[Average Review Score]`, `[Total Orders]` (as tooltip)
- Filter: `[Total Orders] >= 100`

**Satisfaction by state** — bar chart
- Axis: `dim_customers[customer_state]`
- Values: `[Average Review Score]`

### Slicers
`dim_date[year]`, `dim_customers[customer_state]`, `dim_products[product_category]`.

---

## Build order

1. Connect to BigQuery, import the six tables, set the four relationships.
2. Mark `dim_date` as the date table.
3. Create every measure from the catalogue and verify against its expected value.
4. Build Page 1 — it exercises the base measures and proves the model.
5. Build Page 2 — the `is_label_eligible` filter is the thing to get right.
6. Build Page 3 — the `customer_unique_id` grouping is the thing to get right.
7. Apply consistent formatting: same colour for revenue, same for late, same for satisfaction.
8. Add a title and a one-line "what this page answers" subtitle to each page.

## Acceptance test

The model is correct when all 20 expected values in the measure catalogue reproduce exactly.
A dashboard that looks right but reports 7.87% late is worse than no dashboard, because the
error is invisible.
