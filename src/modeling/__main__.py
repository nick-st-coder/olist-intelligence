"""Command line for the delivery-delay model.

    python -m src.modeling train          run the full experiment, log to MLflow
    python -m src.modeling train --fast   one Optuna trial per tuned model, no tracking
    python -m src.modeling explain        global SHAP importance for the saved pipeline
    python -m src.modeling score          score the test window with the saved pipeline

The training command is the only one that reads the test window, and it reads it once.
"""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd
from src.config import ORDER_LEVEL_PATH
from src.features.build_features import ALL_FEATURES, build_modelling_frame
from src.modeling.explain import global_importance, grouped_importance
from src.modeling.predict import load_metadata, load_pipeline, predict_orders
from src.modeling.splitting import chronological_split, load_model_config
from src.modeling.train import run_training

logger = logging.getLogger(__name__)


def _load_order_level() -> pd.DataFrame:
    if not ORDER_LEVEL_PATH.exists():
        raise FileNotFoundError(
            f"{ORDER_LEVEL_PATH} not found — build the analytical dataset first "
            "(python -m src.preprocessing)"
        )
    return pd.read_parquet(ORDER_LEVEL_PATH)


def _print_table(title: str, frame: pd.DataFrame, *, float_format: str = "%.4f") -> None:
    print(f"\n{title}\n{'=' * len(title)}")
    print(frame.to_string(float_format=lambda v: float_format % v))


def cmd_train(args: argparse.Namespace) -> int:
    order_level = _load_order_level()
    config = load_model_config()
    if args.fast:
        config["tuning"]["n_trials"] = 1
        config["tuning"]["timeout_seconds"] = None

    result = run_training(
        order_level,
        config,
        models=args.models,
        tune=args.tune,
        track=not args.no_tracking,
    )

    _print_table("split", result.split_summary)
    _print_table(
        "validation — baselines, each at its cost-optimal threshold", result.baseline_results
    )
    _print_table("validation — tuned, each at its cost-optimal threshold", result.tuned_results)
    _print_table(
        "test — selected model", pd.DataFrame([result.test_metrics]).T.rename(columns={0: "value"})
    )
    _print_table("cost sensitivity on validation", result.cost_sensitivity)
    _print_table("top feature importances", result.feature_importance.head(15), float_format="%.5f")

    print(f"\nselected model: {result.best_model_name}  threshold: {result.threshold:.2f}")
    if result.run_id:
        print(f"mlflow run: {result.run_id}")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    pipeline = load_pipeline()
    order_level = _load_order_level()
    frame = build_modelling_frame(order_level)
    features = frame[list(ALL_FEATURES)]
    importance = global_importance(pipeline, features, max_rows=args.max_rows)
    _print_table("SHAP — transformed columns", importance.head(20), float_format="%.5f")
    _print_table(
        "SHAP — grouped by source feature",
        grouped_importance(importance).head(15),
        float_format="%.5f",
    )
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    pipeline = load_pipeline()
    order_level = _load_order_level()
    frame = build_modelling_frame(order_level)
    split = chronological_split(frame)

    threshold = args.threshold
    if threshold is None:
        threshold = load_metadata().get("threshold", 0.5)

    predictions = predict_orders(pipeline, split.test[list(ALL_FEATURES)], threshold=threshold)
    _print_table(
        "risk band distribution on test",
        predictions.frame["risk_band"].value_counts().to_frame("orders"),
    )
    print(f"\nscored {len(predictions.frame):,} orders at threshold {threshold:.2f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.modeling", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    train = sub.add_parser("train", help="run the experiment end to end")
    train.add_argument("--models", nargs="*", default=None, help="subset of models to fit")
    train.add_argument("--tune", nargs="*", default=None, help="subset of models to tune")
    train.add_argument("--fast", action="store_true", help="one Optuna trial per tuned model")
    train.add_argument("--no-tracking", action="store_true", help="skip MLflow logging")
    train.set_defaults(func=cmd_train)

    explain = sub.add_parser("explain", help="global SHAP importance for the saved model")
    explain.add_argument("--max-rows", type=int, default=2000)
    explain.set_defaults(func=cmd_explain)

    score = sub.add_parser("score", help="score the test window")
    score.add_argument("--threshold", type=float, default=None)
    score.set_defaults(func=cmd_score)
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
