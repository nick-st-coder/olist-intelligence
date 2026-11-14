"""Chronological candidate evaluation and gated MLflow registration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score
from src.monitoring.performance import performance_summary

logger = logging.getLogger(__name__)


def compare_candidate(
    labels: np.ndarray,
    production_probabilities: np.ndarray,
    candidate_probabilities: np.ndarray,
    *,
    threshold: float,
    minimum_pr_auc_lift: float,
) -> dict[str, Any]:
    """Compare two models on one untouched chronological evaluation window.

    The candidate passes only if its PR-AUC clears the configured relative lift over production.
    PR-AUC is reported alongside the evaluation base rate because this target is imbalanced.
    """
    if minimum_pr_auc_lift < 0:
        raise ValueError("minimum_pr_auc_lift cannot be negative")
    labels = np.asarray(labels, dtype=int)
    production_probabilities = np.asarray(production_probabilities, dtype=float)
    candidate_probabilities = np.asarray(candidate_probabilities, dtype=float)
    if not len(labels) or not (
        len(labels) == len(production_probabilities) == len(candidate_probabilities)
    ):
        raise ValueError("labels and both probability arrays must have the same non-zero length")
    if not np.isin(labels, [0, 1]).all() or len(np.unique(labels)) < 2:
        raise ValueError("retraining comparison requires both 0 and 1 labels")
    for name, probabilities in (
        ("production", production_probabilities),
        ("candidate", candidate_probabilities),
    ):
        if not np.isfinite(probabilities).all() or (
            (probabilities < 0) | (probabilities > 1)
        ).any():
            raise ValueError(f"{name} probabilities must be finite values between 0 and 1")

    base_rate = float(labels.mean())
    production_pr_auc = float(average_precision_score(labels, production_probabilities))
    candidate_pr_auc = float(average_precision_score(labels, candidate_probabilities))
    required_pr_auc = production_pr_auc * (1.0 + minimum_pr_auc_lift)
    accepted = candidate_pr_auc >= required_pr_auc
    result = {
        "status": "accepted" if accepted else "rejected",
        "evaluation_orders": len(labels),
        "base_rate": base_rate,
        "production": {
            "pr_auc": production_pr_auc,
            "pr_auc_lift_over_base_rate": production_pr_auc / base_rate,
            **performance_summary(
                _frame(labels, production_probabilities),
                threshold=threshold,
            ),
        },
        "candidate": {
            "pr_auc": candidate_pr_auc,
            "pr_auc_lift_over_base_rate": candidate_pr_auc / base_rate,
            **performance_summary(
                _frame(labels, candidate_probabilities),
                threshold=threshold,
            ),
        },
        "minimum_pr_auc_lift": float(minimum_pr_auc_lift),
        "required_candidate_pr_auc": required_pr_auc,
        "registered": False,
    }
    logger.info(
        "retraining candidate %s: production PR-AUC %.4f, candidate %.4f, base rate %.4f",
        result["status"],
        production_pr_auc,
        candidate_pr_auc,
        base_rate,
    )
    return result


def register_candidate(
    pipeline,
    comparison: dict[str, Any],
    *,
    registered_model: str,
    tracking_db: Path,
    experiment: str = "delivery-delay",
    input_example=None,
) -> dict[str, Any]:
    """Register a candidate in MLflow, refusing to register a failed comparison."""
    if comparison.get("status") != "accepted":
        raise ValueError("candidate did not pass the PR-AUC gate; refusing registration")
    if not registered_model:
        raise ValueError("registered_model must be configured")

    try:
        import mlflow
        import mlflow.sklearn
    except ImportError as exc:
        raise ImportError(
            "MLflow is required to register an accepted candidate; "
            "install with `uv sync --group monitoring`"
        ) from exc

    tracking_db.parent.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{tracking_db.resolve().as_posix()}")
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name="simulated-monitoring-candidate") as run:
        mlflow.log_params(
            {
                "monitoring.simulated": True,
                "monitoring.minimum_pr_auc_lift": comparison["minimum_pr_auc_lift"],
                "monitoring.evaluation_orders": comparison["evaluation_orders"],
            }
        )
        mlflow.log_metrics(
            {
                "production_pr_auc": comparison["production"]["pr_auc"],
                "candidate_pr_auc": comparison["candidate"]["pr_auc"],
                "evaluation_base_rate": comparison["base_rate"],
            }
        )
        model_info = mlflow.sklearn.log_model(
            sk_model=pipeline,
            artifact_path="model",
            registered_model_name=registered_model,
            input_example=input_example,
            serialization_format="pickle",
        )

    result = dict(comparison)
    result["registered"] = True
    result["mlflow_run_id"] = run.info.run_id
    result["registered_model_uri"] = model_info.model_uri
    return result


def _frame(labels: np.ndarray, probabilities: np.ndarray):
    import pandas as pd

    return pd.DataFrame({"is_late": labels, "delay_probability": probabilities})
