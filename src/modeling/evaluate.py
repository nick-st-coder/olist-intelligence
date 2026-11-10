"""Classification metrics and cost-based threshold selection.

The late-delivery problem is imbalanced (8.1% positives), so **PR-AUC** is the primary ranking
metric and accuracy is never reported as a headline. The decision threshold is chosen on the
validation window with an explicit cost matrix — a missed late order costs ten times as much as
an unnecessary alarm — and is then frozen before the test window is touched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

logger = logging.getLogger(__name__)

METRIC_NAMES: tuple[str, ...] = (
    "roc_auc",
    "pr_auc",
    "precision",
    "recall",
    "f1",
    "accuracy",
    "threshold",
    "tn",
    "fp",
    "fn",
    "tp",
    "cost",
    "cost_per_order",
)


@dataclass(frozen=True)
class CostMatrix:
    """Relative cost of each error type."""

    false_negative: float = 10.0
    false_positive: float = 1.0


def compute_metrics(
    y_true: pd.Series | np.ndarray,
    y_proba: np.ndarray,
    *,
    threshold: float = 0.5,
    cost: CostMatrix | None = None,
) -> dict[str, float]:
    """Full metric bundle at a fixed threshold, plus the two ranking metrics.

    ``cost`` is ``FN * (missed late orders) + FP * (false alarms)``; ``cost_per_order`` divides
    it by the number of scored orders so runs of different sizes stay comparable.
    """
    cost = cost or CostMatrix()
    y_true = np.asarray(y_true).astype(int)
    y_proba = np.asarray(y_proba, dtype=float)
    y_pred = (y_proba >= threshold).astype(int)

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    total_cost = cost.false_negative * fn + cost.false_positive * fp

    return {
        "roc_auc": float(roc_auc_score(y_true, y_proba)),
        "pr_auc": float(average_precision_score(y_true, y_proba)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "accuracy": float((tp + tn) / len(y_true)),
        "threshold": float(threshold),
        "tn": float(tn),
        "fp": float(fp),
        "fn": float(fn),
        "tp": float(tp),
        "cost": float(total_cost),
        "cost_per_order": float(total_cost / len(y_true)),
    }


def threshold_curve(
    y_true: pd.Series | np.ndarray,
    y_proba: np.ndarray,
    *,
    cost: CostMatrix | None = None,
    step: float = 0.01,
) -> pd.DataFrame:
    """Metrics at every threshold on a coarse grid, for plotting and for the optimum search."""
    cost = cost or CostMatrix()
    thresholds = np.round(np.arange(step, 1.0, step), 4)
    rows = [compute_metrics(y_true, y_proba, threshold=float(t), cost=cost) for t in thresholds]
    return pd.DataFrame(rows)


def best_cost_threshold(
    y_true: pd.Series | np.ndarray,
    y_proba: np.ndarray,
    *,
    cost: CostMatrix | None = None,
    step: float = 0.01,
) -> tuple[float, pd.DataFrame]:
    """Return the threshold minimising the business cost on **validation** data only.

    Ties are broken towards the lower threshold, which catches more late orders at equal cost.
    The full curve is returned so the choice can be plotted and audited.
    """
    curve = threshold_curve(y_true, y_proba, cost=cost, step=step)
    best = curve.sort_values(["cost", "threshold"], ascending=[True, True]).iloc[0]
    logger.info(
        "cost-optimal threshold %.2f (cost %.0f, precision %.3f, recall %.3f)",
        best["threshold"],
        best["cost"],
        best["precision"],
        best["recall"],
    )
    return float(best["threshold"]), curve


def metrics_frame(results: dict[str, dict[str, float]]) -> pd.DataFrame:
    """Turn ``{partition: metrics}`` into a readable table."""
    frame = pd.DataFrame(results).T
    ordered = [c for c in METRIC_NAMES if c in frame.columns]
    return frame[ordered].astype(float)


def cost_sensitivity(
    y_true: pd.Series | np.ndarray,
    y_proba: np.ndarray,
    *,
    ratios: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0),
    step: float = 0.01,
) -> pd.DataFrame:
    """How the optimal threshold moves as the miss-to-alarm cost ratio changes.

    Documented because "the threshold" is not a property of the model alone — it is a statement
    about how much a missed delivery costs the business relative to an unnecessary intervention.
    """
    rows = []
    for ratio in ratios:
        cost = CostMatrix(false_negative=ratio, false_positive=1.0)
        threshold, curve = best_cost_threshold(y_true, y_proba, cost=cost, step=step)
        best = curve.loc[curve["threshold"] == threshold].iloc[0]
        rows.append(
            {
                "cost_ratio": ratio,
                "threshold": threshold,
                "precision": best["precision"],
                "recall": best["recall"],
                "f1": best["f1"],
                "cost_per_order": best["cost_per_order"],
            }
        )
    return pd.DataFrame(rows)
