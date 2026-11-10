"""Tests for the metrics, the cost-based threshold and the risk bands."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.modeling.evaluate import (
    CostMatrix,
    best_cost_threshold,
    compute_metrics,
    cost_sensitivity,
    metrics_frame,
    threshold_curve,
)
from src.modeling.predict import risk_band


def _separable(n: int = 1000, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.1).astype(int)
    proba = np.clip(0.05 + 0.6 * y + rng.normal(0, 0.15, n), 0, 1)
    return y, proba


def test_metrics_are_bounded_and_consistent() -> None:
    y, proba = _separable()
    metrics = compute_metrics(y, proba, threshold=0.5)
    for key in ("roc_auc", "pr_auc", "precision", "recall", "f1", "accuracy"):
        assert 0.0 <= metrics[key] <= 1.0
    assert metrics["tp"] + metrics["fp"] + metrics["tn"] + metrics["fn"] == len(y)


def test_confusion_matrix_counts_match_the_threshold() -> None:
    y = np.array([0, 0, 1, 1])
    proba = np.array([0.1, 0.6, 0.4, 0.9])
    metrics = compute_metrics(y, proba, threshold=0.5)
    assert (metrics["tn"], metrics["fp"], metrics["fn"], metrics["tp"]) == (1, 1, 1, 1)


def test_cost_uses_the_matrix() -> None:
    y = np.array([0, 0, 1, 1])
    proba = np.array([0.1, 0.6, 0.4, 0.9])
    metrics = compute_metrics(y, proba, threshold=0.5, cost=CostMatrix(10.0, 1.0))
    # one false positive (cost 1) and one false negative (cost 10)
    assert metrics["cost"] == pytest.approx(11.0)
    assert metrics["cost_per_order"] == pytest.approx(11.0 / 4)


def test_a_perfect_ranking_has_pr_auc_one() -> None:
    y = np.array([0, 0, 1, 1])
    proba = np.array([0.1, 0.2, 0.8, 0.9])
    assert compute_metrics(y, proba)["pr_auc"] == pytest.approx(1.0)


def test_best_threshold_minimises_cost() -> None:
    y, proba = _separable()
    threshold, curve = best_cost_threshold(y, proba, cost=CostMatrix(10.0, 1.0))
    assert curve["cost"].min() == pytest.approx(
        curve.loc[curve["threshold"] == threshold, "cost"].iloc[0]
    )


def test_a_higher_miss_cost_lowers_the_threshold() -> None:
    """If missing a late order hurts more, the model should flag more orders."""
    y, proba = _separable()
    cheap, _ = best_cost_threshold(y, proba, cost=CostMatrix(2.0, 1.0))
    expensive, _ = best_cost_threshold(y, proba, cost=CostMatrix(50.0, 1.0))
    assert expensive <= cheap


def test_threshold_curve_covers_the_unit_interval() -> None:
    y, proba = _separable(200)
    curve = threshold_curve(y, proba, step=0.1)
    assert curve["threshold"].min() == pytest.approx(0.1)
    assert curve["threshold"].max() < 1.0
    assert len(curve) == 9


def test_cost_sensitivity_is_monotonic_in_recall() -> None:
    y, proba = _separable()
    table = cost_sensitivity(y, proba, ratios=(1.0, 10.0, 50.0))
    assert table["recall"].is_monotonic_increasing
    assert table["threshold"].is_monotonic_decreasing


def test_metrics_frame_orders_the_columns() -> None:
    y, proba = _separable(200)
    frame = metrics_frame({"a": compute_metrics(y, proba), "b": compute_metrics(y, proba)})
    assert list(frame.index) == ["a", "b"]
    assert frame.columns[0] == "roc_auc"
    assert frame.columns[1] == "pr_auc"


def test_risk_bands_are_ordered() -> None:
    assert risk_band(0.01) == "low"
    assert risk_band(0.45) == "medium"
    assert risk_band(0.95) == "high"


def test_risk_bands_follow_the_threshold() -> None:
    # The band must agree with the decision: anything at or above the threshold is "high".
    assert risk_band(0.2, threshold=0.15) == "high"
    assert risk_band(0.15, threshold=0.15) == "high"
    assert risk_band(0.1, threshold=0.15) == "medium"
    assert risk_band(0.02, threshold=0.15) == "low"
    # The same probability lands in a different band under a different threshold.
    assert risk_band(0.2, threshold=0.6) == "low"


def test_metrics_accept_a_pandas_series() -> None:
    y, proba = _separable(200)
    metrics = compute_metrics(pd.Series(y), proba)
    assert metrics["pr_auc"] > 0.5
