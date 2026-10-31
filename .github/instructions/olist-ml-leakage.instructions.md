---
description: "Use when building features, training, evaluating or reviewing the Olist delivery-delay model: target definition, leakage-safe historical features, chronological splits, threshold tuning, metrics. Covers data leakage prevention and temporal validation rules."
name: "Olist ML Leakage & Temporal Validation"
---

# Leakage & Temporal Validation Rules (Olist delivery-delay model)

## Target

```
is_late = 1 if order_delivered_customer_date > order_estimated_delivery_date else 0
```
Only rows with both dates present and `order_status == 'delivered'` are eligible for **training labels**.
Prediction timestamp = `order_purchase_timestamp`. Every feature must be knowable at that instant.

## Forbidden features (hard block)

- `order_delivered_customer_date`, `order_delivered_carrier_date`, `order_approved_at`
- `order_estimated_delivery_date` **as a raw date** — allowed only as derived *lead time* (`estimated - purchase`, in days), which is known at purchase
- `delivery_days`, `delay_days`, `shipping_limit_date` (post-purchase)
- `review_score`, `review_comment_*`, `review_creation_date`
- `order_status`
- Any aggregate computed over the full dataset (global means, target encoding on full data)

## Historical seller features — correct pattern

Compute with time-ordered expanding statistics, shifted by one order:

```python
df = df.sort_values(["seller_id", "order_purchase_timestamp"])
df["seller_prior_orders"] = df.groupby("seller_id").cumcount()
df["seller_prior_late_rate"] = (
    df.groupby("seller_id")["is_late"]
      .transform(lambda s: s.shift().expanding().mean())
)
```
Rules:
- Always `shift()` before `expanding()`/`rolling()` — the current order must never see its own label.
- Use `min_periods` and a fallback (global prior or `NaN` + imputer) for sellers with no history.
- If a feature needs the label, it can only be built on the **training window**; recompute per fold for CV.

## Splits

- Chronological: e.g. train ≤ 2018-04, validation 2018-05..2018-07, test ≥ 2018-08 (adjust to actual data density; document the cutoffs).
- Temporal CV: `TimeSeriesSplit` or expanding-window folds; never `KFold(shuffle=True)`.
- Fit all preprocessing (imputers, encoders, scalers) **inside** the fold/pipeline.

## Metrics & threshold

Report Precision, Recall, F1, ROC-AUC, PR-AUC, confusion matrix. PR-AUC is primary (class imbalance).
Tune the decision threshold on validation only, using an explicit business cost matrix (cost of a missed late order vs. cost of a false alarm). Never tune on test.

## Review checklist

Before accepting a model or feature set, verify:
1. No forbidden column in the feature list (grep the feature builder).
2. Feature timestamps ≤ prediction timestamp.
3. Preprocessing fitted on train only.
4. Test set touched exactly once.
5. Baseline (Dummy + Logistic Regression) numbers recorded for comparison.
