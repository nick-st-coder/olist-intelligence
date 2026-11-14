"""Delayed-label classification performance and probability calibration summaries."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, confusion_matrix
from src.modeling.evaluate import CostMatrix, compute_metrics

logger = logging.getLogger(__name__)


def performance_summary(
    frame: pd.DataFrame,
    *,
    threshold: float,
    cost: CostMatrix | None = None,
    calibration_bins: int = 10,
) -> dict[str, Any]:
    """Summarise outcomes after labels arrive, including PR-AUC relative to the base rate."""
    if calibration_bins < 1:
        raise ValueError("calibration_bins must be positive")
    required = {"is_late", "delay_probability"}
    missing = required - set(frame.columns)
    if missing:
        raise KeyError(f"performance frame is missing columns: {sorted(missing)}")

    labelled = frame.loc[frame["is_late"].notna()].copy()
    if labelled.empty:
        return {
            "status": "awaiting_labels",
            "labelled_orders": 0,
            "threshold": float(threshold),
            "pr_auc": None,
            "base_rate": None,
            "pr_auc_lift_over_base_rate": None,
            "calibration": [],
        }

    y = pd.to_numeric(labelled["is_late"], errors="raise").to_numpy(dtype=int)
    probability = pd.to_numeric(labelled["delay_probability"], errors="raise").to_numpy(
        dtype=float
    )
    if not np.isin(y, [0, 1]).all():
        raise ValueError("is_late must contain only 0/1 labels")
    if not np.isfinite(probability).all() or ((probability < 0) | (probability > 1)).any():
        raise ValueError("delay_probability must contain finite values between 0 and 1")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")

    base_rate = float(y.mean())
    positives = int(y.sum())
    negatives = len(y) - positives
    calibration = _calibration_table(y, probability, calibration_bins)
    summary: dict[str, Any] = {
        "status": "ready" if positives and negatives else "insufficient_classes",
        "labelled_orders": len(y),
        "positive_labels": positives,
        "negative_labels": negatives,
        "base_rate": base_rate,
        "threshold": float(threshold),
        "calibration": calibration,
        "brier_score": float(brier_score_loss(y, probability)),
    }
    if positives and negatives:
        metrics = compute_metrics(y, probability, threshold=threshold, cost=cost)
        pr_auc = float(average_precision_score(y, probability))
        summary.update(
            {
                **metrics,
                "pr_auc": pr_auc,
                "pr_auc_lift_over_base_rate": pr_auc / base_rate if base_rate else None,
                "confusion_matrix": {
                    "tn": int(metrics["tn"]),
                    "fp": int(metrics["fp"]),
                    "fn": int(metrics["fn"]),
                    "tp": int(metrics["tp"]),
                },
            }
        )
    else:
        summary.update(
            {
                "pr_auc": None,
                "pr_auc_lift_over_base_rate": None,
                "roc_auc": None,
                "precision": None,
                "recall": None,
                "f1": None,
                "confusion_matrix": _confusion_counts(y, probability, threshold),
            }
        )

    logger.info(
        "delayed-label performance: %s orders, base rate %.3f, PR-AUC %s",
        len(y),
        base_rate,
        summary["pr_auc"],
    )
    return summary


def _calibration_table(
    labels: np.ndarray, probabilities: np.ndarray, bins: int
) -> list[dict[str, float | int]]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    assignments = np.minimum(np.digitize(probabilities, edges[1:-1]), bins - 1)
    rows = []
    for index in range(bins):
        mask = assignments == index
        if mask.any():
            rows.append(
                {
                    "bin": index,
                    "count": int(mask.sum()),
                    "mean_predicted_probability": float(probabilities[mask].mean()),
                    "observed_late_rate": float(labels[mask].mean()),
                }
            )
    return rows


def _confusion_counts(
    labels: np.ndarray, probabilities: np.ndarray, threshold: float
) -> dict[str, int]:
    predictions = (probabilities >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(labels, predictions, labels=[0, 1]).ravel()
    return {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}
