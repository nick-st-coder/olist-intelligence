"""Focused tests for simulated drift, delayed-label performance and retraining gates."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.features.build_features import (
    ALL_FEATURES,
    BOOLEAN_FEATURES,
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
)
from src.monitoring.drift import generate_drift_report
from src.monitoring.performance import performance_summary
from src.monitoring.retraining import compare_candidate, register_candidate
from src.monitoring.simulation import build_simulated_stream, simulation_cycles


def _monitoring_frame(rows: int = 80, *, shift: bool = False) -> pd.DataFrame:
    frame = pd.DataFrame(index=range(rows))
    for column in NUMERIC_FEATURES:
        frame[column] = np.linspace(0.0, 1.0, rows)
    for column in CATEGORICAL_FEATURES:
        frame[column] = "baseline"
    for column in set(ALL_FEATURES) - set(NUMERIC_FEATURES) - set(CATEGORICAL_FEATURES):
        frame[column] = 0.0
    frame["delay_probability"] = np.linspace(0.01, 0.4, rows)
    if shift:
        frame["item_revenue"] += 10_000.0
        frame["product_category"] = "new-category"
    return frame.loc[:, [*ALL_FEATURES, "delay_probability"]]


class _ProbabilityModel:
    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        probability = np.linspace(0.1, 0.9, len(features))
        return np.column_stack([1.0 - probability, probability])


def _order_level() -> pd.DataFrame:
    rows = []
    for purchase, delivered, label in (
        ("2018-06-05", "2018-06-10", 0),
        ("2018-06-20", "2018-07-15", 1),
        ("2018-07-06", "2018-07-08", 0),
    ):
        row = dict.fromkeys(NUMERIC_FEATURES, 1.0)
        row.update(dict.fromkeys(BOOLEAN_FEATURES, 0.0))
        row.update(dict.fromkeys(CATEGORICAL_FEATURES, "baseline"))
        row.update(
            {
                "seller_has_history": 0.0,
                "order_id": f"order-{len(rows)}",
                "order_purchase_timestamp": pd.Timestamp(purchase),
                "order_delivered_customer_date": pd.Timestamp(delivered),
                "is_late": label,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def test_drift_report_writes_evidently_and_summary_outputs(tmp_path) -> None:
    current = _monitoring_frame(shift=True)
    current.loc[0, "estimated_lead_days"] = np.nan
    summary = generate_drift_report(
        _monitoring_frame(),
        current,
        tmp_path,
        batch_id="2018-06",
    )

    assert summary["simulation"] is True
    assert summary["current_rows"] == 80
    assert "item_revenue" in summary["drifted_features"]
    assert summary["new_categories"]["product_category"] == ["new-category"]
    assert summary["missing_values"]["estimated_lead_days"] == 1
    assert (tmp_path / "drift.html").is_file()
    assert "SIMULATED HISTORICAL REPLAY" in (tmp_path / "drift.html").read_text(
        encoding="utf-8"
    )
    assert (tmp_path / "evidently-report.json").is_file()
    assert (tmp_path / "summary.json").is_file()


def test_drift_report_rejects_missing_model_features(tmp_path) -> None:
    reference = _monitoring_frame().drop(columns="item_revenue")
    with pytest.raises(KeyError, match="item_revenue"):
        generate_drift_report(reference, _monitoring_frame(), tmp_path, batch_id="2018-06")


def test_simulation_exposes_labels_only_after_delivery_and_delay() -> None:
    stream = build_simulated_stream(
        _order_level(),
        _ProbabilityModel(),
        threshold=0.5,
        start="2018-06-01",
        end="2018-07-31",
        label_delay_days=30,
    )
    cycles = list(simulation_cycles(stream, label_delay_days=30))

    assert [cycle.batch_id for cycle in cycles] == ["2018-06", "2018-07"]
    assert len(cycles[0].batch) == 2
    assert len(cycles[0].matured) == 1
    assert len(cycles[1].matured) == 3
    assert stream["simulated"].all()


def test_performance_reports_pr_auc_against_base_rate_and_calibration() -> None:
    frame = pd.DataFrame(
        {
            "is_late": [0, 0, 1, 1],
            "delay_probability": [0.1, 0.2, 0.7, 0.9],
        }
    )
    summary = performance_summary(frame, threshold=0.5, calibration_bins=2)

    assert summary["status"] == "ready"
    assert summary["pr_auc"] == pytest.approx(1.0)
    assert summary["base_rate"] == pytest.approx(0.5)
    assert summary["pr_auc_lift_over_base_rate"] == pytest.approx(2.0)
    assert summary["confusion_matrix"] == {"tn": 2, "fp": 0, "fn": 0, "tp": 2}
    assert summary["brier_score"] < 0.1
    assert summary["calibration"]


def test_performance_handles_no_labels_and_rejects_invalid_probabilities() -> None:
    empty = pd.DataFrame(columns=["is_late", "delay_probability"])
    assert performance_summary(empty, threshold=0.5)["status"] == "awaiting_labels"
    invalid = pd.DataFrame({"is_late": [0], "delay_probability": [1.1]})
    with pytest.raises(ValueError, match="between 0 and 1"):
        performance_summary(invalid, threshold=0.5)


def test_candidate_gate_requires_configured_relative_pr_auc_lift(tmp_path) -> None:
    labels = np.array([0, 0, 0, 1, 1, 1])
    production = np.array([0.6, 0.4, 0.5, 0.4, 0.6, 0.5])
    better = np.array([0.1, 0.2, 0.3, 0.8, 0.9, 0.7])
    result = compare_candidate(
        labels,
        production,
        better,
        threshold=0.5,
        minimum_pr_auc_lift=0.05,
    )

    assert result["status"] == "accepted"
    assert result["candidate"]["pr_auc_lift_over_base_rate"] > 1.0
    assert result["registered"] is False
    with pytest.raises(ValueError, match="refusing registration"):
        register_candidate(
            object(),
            {**result, "status": "rejected"},
            registered_model="delivery-delay",
            tracking_db=tmp_path / "mlflow.db",
        )


def test_accepted_candidate_is_registered_in_temporary_mlflow_registry(tmp_path) -> None:
    import mlflow
    from sklearn.linear_model import LogisticRegression

    features = pd.DataFrame({"value": [0.0, 1.0, 2.0, 3.0]})
    model = LogisticRegression().fit(features, [0, 0, 1, 1])
    comparison = {
        "status": "accepted",
        "minimum_pr_auc_lift": 0.05,
        "evaluation_orders": 4,
        "production": {"pr_auc": 0.5},
        "candidate": {"pr_auc": 0.8},
        "base_rate": 0.5,
    }
    tracking_db = tmp_path / "mlflow.db"
    tracking_uri = f"sqlite:///{tracking_db.as_posix()}"
    previous_tracking_uri = mlflow.get_tracking_uri()
    try:
        result = register_candidate(
            model,
            comparison,
            registered_model="monitoring-test-model",
            tracking_db=tracking_db,
            experiment="monitoring-test",
            input_example=features.head(2),
        )
        client = mlflow.MlflowClient(tracking_uri=tracking_uri)
        versions = client.search_model_versions("name = 'monitoring-test-model'")
    finally:
        mlflow.set_tracking_uri(previous_tracking_uri)

    assert result["registered"] is True
    assert result["registered_model_uri"].startswith("models:/")
    assert len(versions) == 1
