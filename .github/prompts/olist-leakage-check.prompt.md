---
description: "Audit the Olist delivery-delay feature set and training pipeline for data leakage and temporal correctness before training or shipping."
name: "Olist Leakage Check"
agent: "Olist Reviewer"
tools: [read, search, execute]
argument-hint: "Optional: path to the feature builder or model config"
---
Audit the delivery-delay model for data leakage.

1. Locate the feature list: `src/features/build_features.py`, `configs/*.yaml`, and any notebook that builds features.
2. **Forbidden columns** — flag any occurrence in the feature set:
   `order_delivered_customer_date`, `order_delivered_carrier_date`, `order_approved_at`, `shipping_limit_date`, `delivery_days`, `delay_days`, `review_score`, `review_comment_*`, `review_creation_date`, `order_status`.
   `order_estimated_delivery_date` is allowed **only** as a derived lead time (`estimated − purchase`).
3. **Historical features** — verify the pattern:
   - sorted by `order_purchase_timestamp` before any `groupby`/`expanding`/`rolling`
   - `shift()` applied so the current order never sees its own label
   - `min_periods` set and a documented fallback for sellers with no history
   - recomputed per CV fold if label-dependent
4. **Splits** — confirm chronological train/val/test and `TimeSeriesSplit`; flag any `KFold(shuffle=True)` or `train_test_split` without `shuffle=False`.
5. **Preprocessing** — confirm imputers/encoders/scalers are inside a `Pipeline` fitted per fold.
6. **Threshold** — confirm it was tuned on validation only and the test set was evaluated once.
7. **Global aggregates** — flag any mean/encoding computed over the full dataset.

Output a table: Check | Status (Pass/Fail) | Evidence (file:line) | Fix. End with a verdict: **Safe to train** or **Blocked**.
