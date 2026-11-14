"""Feature and prediction drift reports for simulated monitoring batches."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from src.features.build_features import ALL_FEATURES, CATEGORICAL_FEATURES, NUMERIC_FEATURES

logger = logging.getLogger(__name__)

PREDICTION_COLUMN = "delay_probability"
REPORT_COLUMNS = (*ALL_FEATURES, PREDICTION_COLUMN)


def _validate_frames(reference: pd.DataFrame, current: pd.DataFrame) -> None:
    if reference.empty or current.empty:
        raise ValueError("reference and current monitoring frames must both contain rows")
    for name, frame in (("reference", reference), ("current", current)):
        missing = set(REPORT_COLUMNS) - set(frame.columns)
        if missing:
            raise KeyError(f"{name} frame is missing monitoring columns: {sorted(missing)}")


def _quality_summary(reference: pd.DataFrame, current: pd.DataFrame) -> dict[str, Any]:
    missing_values = {
        column: int(current[column].isna().sum()) for column in ALL_FEATURES
    }
    invalid_values: dict[str, int] = {}
    for column in NUMERIC_FEATURES:
        values = pd.to_numeric(current[column], errors="coerce")
        invalid = current[column].notna() & values.isna()
        invalid |= values.isin([np.inf, -np.inf])
        invalid_values[column] = int(invalid.sum())

    new_categories = {}
    for column in CATEGORICAL_FEATURES:
        known = set(reference[column].dropna().astype(str))
        observed = set(current[column].dropna().astype(str))
        new_categories[column] = sorted(observed - known)

    return {
        "missing_values": missing_values,
        "invalid_values": invalid_values,
        "new_categories": new_categories,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (np.bool_, np.integer, np.floating)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialise {type(value).__name__}")


def generate_drift_report(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    output_dir: Path,
    *,
    batch_id: str,
    drift_share_threshold: float = 0.5,
) -> dict[str, Any]:
    """Write Evidently HTML/JSON reports and return a compact, machine-readable summary.

    ``reference`` and ``current`` must contain all model features plus
    ``delay_probability``. The Olist replay is historical, so every report is explicitly marked
    simulated and must not be interpreted as a live production alert.
    """
    _validate_frames(reference, current)
    if not 0.0 <= drift_share_threshold <= 1.0:
        raise ValueError("drift_share_threshold must be between 0 and 1")

    try:
        from evidently.legacy.metric_preset import DataDriftPreset, DataQualityPreset
        from evidently.legacy.report import Report
    except ImportError as exc:
        raise ImportError(
            "Evidently is required for drift reports; install with `uv sync --group monitoring`"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    reference_data = reference.loc[:, list(REPORT_COLUMNS)].copy()
    current_data = current.loc[:, list(REPORT_COLUMNS)].copy()
    report = Report(
        name=f"SIMULATED Olist drift report — {batch_id}",
        tags=["simulated", "historical-replay"],
        metadata={"simulation": "true", "batch_id": batch_id},
        metrics=[
            DataDriftPreset(
                columns=list(REPORT_COLUMNS),
                drift_share=drift_share_threshold,
            ),
            DataQualityPreset(columns=list(ALL_FEATURES)),
        ],
    )
    report.run(reference_data=reference_data, current_data=current_data)

    raw_report = json.loads(report.json())
    drift_table = next(
        (
            metric["result"]
            for metric in raw_report["metrics"]
            if metric.get("metric") == "DataDriftTable"
        ),
        None,
    )
    if drift_table is None:
        raise RuntimeError("Evidently report did not produce a DataDriftTable")

    column_results = drift_table.get("drift_by_columns", {})
    drifted_features = sorted(
        column
        for column, result in column_results.items()
        if column != PREDICTION_COLUMN and result.get("drift_detected", False)
    )
    prediction_result = column_results.get(PREDICTION_COLUMN, {})
    summary = {
        "simulation": True,
        "batch_id": batch_id,
        "reference_rows": len(reference_data),
        "current_rows": len(current_data),
        "dataset_drift": bool(drift_table.get("dataset_drift", False)),
        "drift_share": float(drift_table.get("share_of_drifted_columns", 0.0)),
        "drifted_features": drifted_features,
        "prediction_drift": bool(prediction_result.get("drift_detected", False)),
        "feature_results": {
            column: {
                "drift_detected": bool(result.get("drift_detected", False)),
                "drift_score": result.get("drift_score"),
                "stattest": result.get("stattest_name"),
            }
            for column, result in column_results.items()
            if column != PREDICTION_COLUMN
        },
        "prediction_result": {
            "drift_detected": bool(prediction_result.get("drift_detected", False)),
            "drift_score": prediction_result.get("drift_score"),
            "stattest": prediction_result.get("stattest_name"),
        },
        **_quality_summary(reference_data, current_data),
    }

    html_path = output_dir / "drift.html"
    report.save_html(str(html_path))
    html = html_path.read_text(encoding="utf-8")
    body_start = html.find("<body")
    body_end = html.find(">", body_start)
    if body_start < 0 or body_end < 0:
        raise RuntimeError("Evidently generated HTML without a body element")
    simulation_banner = (
        '<div style="position:sticky;top:0;z-index:10000;padding:12px 18px;'
        "background:#fff3cd;color:#664d03;border-bottom:1px solid #ffecb5;"
        'font:600 14px sans-serif;">SIMULATED HISTORICAL REPLAY — '
        f"batch {batch_id}; this is not live production telemetry.</div>"
    )
    html_path.write_text(html[: body_end + 1] + simulation_banner + html[body_end + 1 :], encoding="utf-8")
    (output_dir / "evidently-report.json").write_text(
        json.dumps(raw_report, indent=2), encoding="utf-8"
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=_jsonable), encoding="utf-8"
    )
    logger.info(
        "simulated drift report for %s: %s/%s columns drifted",
        batch_id,
        len(drifted_features),
        len(ALL_FEATURES),
    )
    return summary
