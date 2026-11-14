"""Run simulated drift reports or a gated candidate-model comparison.

    python -m src.monitoring run
    python -m src.monitoring retrain
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from src.config import ORDER_LEVEL_PATH, PROJECT_ROOT
from src.features.build_features import ALL_FEATURES, build_modelling_frame
from src.modeling.predict import (
    DEFAULT_MODEL_PATH,
    load_metadata,
    load_pipeline,
    model_fingerprint,
    predict_orders,
)
from src.modeling.splitting import load_model_config
from src.monitoring.drift import generate_drift_report
from src.monitoring.performance import performance_summary
from src.monitoring.retraining import compare_candidate, register_candidate
from src.monitoring.simulation import build_simulated_stream, simulation_cycles

logger = logging.getLogger(__name__)
MONITORING_CONFIG_PATH = PROJECT_ROOT / "configs" / "monitoring.yaml"


def _load_monitoring_config() -> dict:
    if not MONITORING_CONFIG_PATH.exists():
        raise FileNotFoundError(f"monitoring config not found: {MONITORING_CONFIG_PATH}")
    config = yaml.safe_load(MONITORING_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"invalid monitoring config: {MONITORING_CONFIG_PATH}")
    return config


def _load_order_level() -> pd.DataFrame:
    if not ORDER_LEVEL_PATH.exists():
        raise FileNotFoundError(
            f"{ORDER_LEVEL_PATH} not found — build the analytical dataset first"
        )
    return pd.read_parquet(ORDER_LEVEL_PATH)


def _with_predictions(frame: pd.DataFrame, pipeline, threshold: float) -> pd.DataFrame:
    predictions = predict_orders(
        pipeline,
        frame.loc[:, list(ALL_FEATURES)],
        threshold=threshold,
    ).frame
    result = frame.loc[:, list(ALL_FEATURES)].copy()
    result["delay_probability"] = predictions["delay_probability"]
    return result


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, default=_json_default, allow_nan=False),
        encoding="utf-8",
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, Path)):
        return value.isoformat() if isinstance(value, pd.Timestamp) else str(value)
    if isinstance(value, (np.bool_, np.integer, np.floating)):
        value = value.item()
        if isinstance(value, float) and not np.isfinite(value):
            return None
        return value
    raise TypeError(f"cannot serialise {type(value).__name__}")


def cmd_run(_args: argparse.Namespace) -> int:
    config = _load_monitoring_config()
    order_level = _load_order_level()
    pipeline = load_pipeline()
    metadata = load_metadata()
    threshold = float(metadata.get("threshold", 0.5))
    frame = build_modelling_frame(order_level)

    reference_config = config["reference"]
    purchase = pd.to_datetime(frame["order_purchase_timestamp"])
    reference_start = pd.Timestamp(reference_config["start"])
    reference_end = pd.Timestamp(reference_config["end"]).normalize() + pd.Timedelta(1, unit="D")
    reference_mask = (purchase >= reference_start) & (purchase < reference_end)
    reference = _with_predictions(frame.loc[reference_mask], pipeline, threshold)
    if reference.empty:
        raise ValueError("configured reference period contains no labelled orders")

    simulation_config = config["simulation"]
    stream = build_simulated_stream(
        order_level,
        pipeline,
        threshold=threshold,
        start=simulation_config["start"],
        end=simulation_config["end"],
        label_delay_days=int(simulation_config["label_delay_days"]),
    )
    output_dir = PROJECT_ROOT / config.get("output_dir", "data/processed/monitoring")
    output_dir.mkdir(parents=True, exist_ok=True)

    batch_results = []
    for cycle in simulation_cycles(
        stream,
        label_delay_days=int(simulation_config["label_delay_days"]),
    ):
        current = cycle.batch.loc[:, [*ALL_FEATURES, "delay_probability"]]
        batch_dir = output_dir / "simulated" / cycle.batch_id
        generate_drift_report(
            reference,
            current,
            batch_dir,
            batch_id=cycle.batch_id,
            drift_share_threshold=float(config.get("drift", {}).get("threshold", 0.5)),
        )
        performance = performance_summary(
            cycle.matured,
            threshold=threshold,
            calibration_bins=10,
        )
        performance.update(
            {
                "simulation": True,
                "batch_id": cycle.batch_id,
                "evaluated_at": cycle.evaluated_at,
                "label_delay_days": int(simulation_config["label_delay_days"]),
            }
        )
        _write_json(batch_dir / "performance.json", performance)
        batch_results.append(
            {
                "batch_id": cycle.batch_id,
                "orders": len(cycle.batch),
                "matured_orders": int(performance["labelled_orders"]),
                "evaluated_at": cycle.evaluated_at,
                "drift_summary": (batch_dir / "summary.json").relative_to(PROJECT_ROOT).as_posix(),
                "performance_summary": (
                    batch_dir / "performance.json"
                ).relative_to(PROJECT_ROOT).as_posix(),
            }
        )

    ledger = (
        stream.groupby("batch_id", sort=True)
        .agg(
            orders=("order_id", "size"),
            first_purchase=("order_purchase_timestamp", "min"),
            last_purchase=("order_purchase_timestamp", "max"),
            first_label_available=("label_available_at", "min"),
            last_label_available=("label_available_at", "max"),
        )
        .reset_index()
    )
    ledger.to_csv(output_dir / "simulated-batch-ledger.csv", index=False)
    manifest = {
        "simulation": True,
        "source": "historical Olist orders replayed chronologically",
        "reference_period": reference_config,
        "simulation_period": simulation_config,
        "model_name": metadata.get("model_name", "unknown"),
        "model_version": model_fingerprint(DEFAULT_MODEL_PATH),
        "threshold": threshold,
        "batches": batch_results,
    }
    _write_json(output_dir / "latest-run.json", manifest)
    print(f"Simulated monitoring reports written to {output_dir}")
    for result in batch_results:
        print(
            f"  {result['batch_id']}: {result['orders']:,} orders, "
            f"{result['matured_orders']:,} labels available"
        )
    return 0


def cmd_retrain(_args: argparse.Namespace) -> int:
    from src.modeling.models import build_model

    monitoring_config = _load_monitoring_config()
    model_config = load_model_config()
    metadata = load_metadata()
    production = load_pipeline()
    order_level = _load_order_level()
    frame = build_modelling_frame(order_level)

    split = model_config["split"]
    test_start = pd.Timestamp(split["validation_end"]).normalize() + pd.Timedelta(1, unit="D")
    test_end = pd.Timestamp(split["test_end"]).normalize() + pd.Timedelta(1, unit="D")
    purchase = pd.to_datetime(frame["order_purchase_timestamp"])
    train_mask = purchase < test_start
    evaluation_mask = (purchase >= test_start) & (purchase < test_end)
    train = frame.loc[train_mask]
    evaluation = frame.loc[evaluation_mask]
    if train.empty or evaluation.empty:
        raise ValueError("candidate retraining requires non-empty pre-test and test windows")

    model_name = str(metadata.get("model_name", "logistic_regression")).split(" (")[0]
    candidate = build_model(model_name, model_config).fit(
        train.loc[:, list(ALL_FEATURES)],
        train["is_late"].astype(int),
    )
    y = evaluation["is_late"].astype(int).to_numpy()
    production_proba = production.predict_proba(evaluation.loc[:, list(ALL_FEATURES)])[:, 1]
    candidate_proba = candidate.predict_proba(evaluation.loc[:, list(ALL_FEATURES)])[:, 1]
    comparison = compare_candidate(
        y,
        production_proba,
        candidate_proba,
        threshold=float(metadata.get("threshold", 0.5)),
        minimum_pr_auc_lift=float(
            monitoring_config.get("retraining", {}).get("min_pr_auc_lift", 0.05)
        ),
    )
    comparison.update(
        {
            "simulation": True,
            "model_name": model_name,
            "production_model_version": model_fingerprint(DEFAULT_MODEL_PATH),
            "candidate_model_family": model_name,
            "candidate_training_end": str(pd.to_datetime(train["order_purchase_timestamp"]).max()),
            "evaluation_start": str(pd.to_datetime(evaluation["order_purchase_timestamp"]).min()),
            "evaluation_end": str(pd.to_datetime(evaluation["order_purchase_timestamp"]).max()),
        }
    )

    output_dir = PROJECT_ROOT / monitoring_config.get(
        "output_dir", "data/processed/monitoring"
    )
    comparison_path = output_dir / "candidate-comparison.json"
    _write_json(comparison_path, comparison)
    if comparison["status"] == "accepted":
        tracking = model_config.get("mlflow", {})
        tracking_db = PROJECT_ROOT / tracking.get("tracking_db", "mlflow.db")
        comparison = register_candidate(
            candidate,
            comparison,
            registered_model=str(tracking.get("registered_model", "")),
            tracking_db=tracking_db,
            experiment=str(tracking.get("experiment", "delivery-delay")),
            input_example=train.loc[:, list(ALL_FEATURES)].head(5),
        )
        _write_json(comparison_path, comparison)
        print(f"Candidate passed the PR-AUC gate and was registered: {comparison['registered_model_uri']}")
    else:
        print("Candidate did not pass the PR-AUC gate; production remains unchanged.")
    print(f"Comparison written to {comparison_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.monitoring", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="replay simulated batches and write monitoring reports")
    run.set_defaults(func=cmd_run)
    retrain = subparsers.add_parser(
        "retrain", help="fit a candidate, compare it on the replay holdout and register if it passes"
    )
    retrain.set_defaults(func=cmd_retrain)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
