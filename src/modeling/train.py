"""Training, temporal cross-validation and experiment tracking.

The flow is deliberately linear and auditable:

1. build the modelling frame (features + label + purchase timestamp)
2. cut it into three contiguous chronological windows
3. fit each candidate on train, score on validation, record metrics at a fixed 0.5 threshold
4. tune the strongest candidates with Optuna on expanding-window folds **inside** train
5. choose the decision threshold on validation using the business cost matrix
6. score the frozen model on test — once — and log everything to MLflow

The test window is read only in the final step of :func:`run_training`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import mlflow
import numpy as np
import optuna
import pandas as pd
from mlflow.models import infer_signature
from mlflow.sklearn import log_model as log_sklearn_model
from sklearn.base import clone
from src.config import PROJECT_ROOT
from src.features.build_features import ALL_FEATURES, build_modelling_frame
from src.modeling.evaluate import (
    CostMatrix,
    best_cost_threshold,
    compute_metrics,
    cost_sensitivity,
    metrics_frame,
)
from src.modeling.models import SEED, build_model, model_names
from src.modeling.predict import save_pipeline
from src.modeling.splitting import (
    chronological_split,
    load_model_config,
    split_windows_from_config,
    temporal_cv_splits,
)

logger = logging.getLogger(__name__)

ARTIFACT_DIR: Path = PROJECT_ROOT / "data" / "processed" / "modeling"
LABEL_COLUMN = "is_late"
PURCHASE_COLUMN = "order_purchase_timestamp"


@dataclass
class TrainingResult:
    """Everything the run produced, kept in memory for the notebooks and the report."""

    config: dict
    split_summary: pd.DataFrame
    baseline_results: pd.DataFrame
    tuned_results: pd.DataFrame
    test_metrics: dict[str, float]
    threshold: float
    cost_curve: pd.DataFrame
    cost_sensitivity: pd.DataFrame
    best_model_name: str
    run_id: str
    feature_importance: pd.DataFrame = field(default_factory=pd.DataFrame)


def _xy(frame: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    features = [c for c in ALL_FEATURES if c in frame.columns]
    return frame[features], frame[LABEL_COLUMN].to_numpy().astype(int)


def _proba(pipeline, frame: pd.DataFrame) -> np.ndarray:
    features, _ = _xy(frame)
    return pipeline.predict_proba(features)[:, 1]


def evaluate_pipeline(
    pipeline,
    frame: pd.DataFrame,
    *,
    cost: CostMatrix,
    threshold: float | None = None,
) -> dict[str, float]:
    """Score a fitted pipeline.

    With ``threshold=None`` the cost-optimal threshold is found on ``frame`` itself, which is
    what makes models comparable: a calibrated model and a class-weighted one put their useful
    operating point in completely different places, so a fixed 0.5 would rank them by
    calibration rather than by skill. Ranking metrics (PR-AUC, ROC-AUC) are threshold-free and
    unaffected. Only ever call this with ``threshold=None`` on validation data.
    """
    _, y = _xy(frame)
    proba = _proba(pipeline, frame)
    if threshold is None:
        threshold, _ = best_cost_threshold(y, proba, cost=cost)
    return compute_metrics(y, proba, threshold=threshold, cost=cost)


def _cv_score(
    pipeline,
    train: pd.DataFrame,
    folds: list[tuple[np.ndarray, np.ndarray]],
    *,
    cost: CostMatrix,
) -> float:
    """Mean PR-AUC across temporal folds. Higher is better."""
    scores = []
    for fit_idx, eval_idx in folds:
        fold_train = train.iloc[fit_idx]
        fold_eval = train.iloc[eval_idx]
        fitted = clone(pipeline).fit(*_xy(fold_train))
        _, y_eval = _xy(fold_eval)
        metrics = compute_metrics(y_eval, _proba(fitted, fold_eval), threshold=0.5, cost=cost)
        scores.append(metrics["pr_auc"])
    return float(np.mean(scores))


def _suggest_params(trial: optuna.Trial, name: str) -> dict:
    """Search space per model family, kept small enough to finish on a laptop."""
    if name == "lightgbm":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 300, 1500, step=100),
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 3, 63),
            "min_child_samples": trial.suggest_int("min_child_samples", 50, 500, step=25),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 50.0, log=True),
        }
    if name == "xgboost":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 300, 1500, step=100),
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
            "max_depth": trial.suggest_int("max_depth", 2, 8),
            "min_child_weight": trial.suggest_float("min_child_weight", 1.0, 100.0, log=True),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 50.0, log=True),
        }
    if name == "random_forest":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 200, 800, step=100),
            "max_depth": trial.suggest_int("max_depth", 4, 24),
            "min_samples_leaf": trial.suggest_int("min_samples_leaf", 10, 300, step=10),
            "max_features": trial.suggest_categorical("max_features", ["sqrt", "log2", 0.5]),
        }
    if name == "logistic_regression":
        return {"C": trial.suggest_float("C", 1e-4, 10.0, log=True)}
    raise ValueError(f"no search space for {name}")


def _tune(
    name: str,
    config: dict,
    train: pd.DataFrame,
    folds: list[tuple[np.ndarray, np.ndarray]],
    *,
    cost: CostMatrix,
) -> tuple[dict, float]:
    """Optuna search on temporal folds of the training window only."""
    tuning = config.get("tuning", {})
    seed = int(config.get("seed", SEED))

    def objective(trial: optuna.Trial) -> float:
        params = _suggest_params(trial, name)
        trial_config = {**config, "models": {**config.get("models", {}), name: params}}
        # Calibration is a monotone transform: it cannot change PR-AUC or ROC-AUC, only where a
        # given threshold lands. Leaving it out of the search keeps each trial to a single fit
        # per fold instead of four.
        pipeline = build_model(name, trial_config, seed=seed, calibrate=False)
        return _cv_score(pipeline, train, folds, cost=cost)

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(
        objective,
        n_trials=int(tuning.get("n_trials", 20)),
        timeout=tuning.get("timeout_seconds"),
        show_progress_bar=False,
    )
    logger.info(
        "optuna %s: best mean fold PR-AUC %.4f in %s trials",
        name,
        study.best_value,
        len(study.trials),
    )
    return study.best_params, float(study.best_value)


def _unwrap(estimator):
    """Peel calibration off an estimator so its native attributes are reachable.

    ``CalibratedClassifierCV`` exposes neither ``feature_importances_`` nor ``coef_``; the
    underlying estimator does, and calibration does not change the fitted coefficients.
    """
    if hasattr(estimator, "calibrated_classifiers_") and estimator.calibrated_classifiers_:
        return estimator.calibrated_classifiers_[0].estimator
    return estimator


def _feature_importance(pipeline) -> pd.DataFrame:
    """Native gain for trees, ``|coef|`` for the linear model."""
    model = _unwrap(pipeline.named_steps["model"])
    feature_names = list(pipeline.named_steps["preprocess"].get_feature_names_out())

    if hasattr(model, "feature_importances_"):
        values = np.asarray(model.feature_importances_, dtype=float)
    elif hasattr(model, "coef_"):
        values = np.abs(np.asarray(model.coef_, dtype=float)).ravel()
    else:
        return pd.DataFrame(columns=["feature", "importance"])

    frame = pd.DataFrame({"feature": feature_names, "importance": values})
    return frame.sort_values("importance", ascending=False, ignore_index=True)


def _write_metadata(
    *,
    path: Path,
    threshold: float,
    best_name: str,
    test_metrics: dict[str, float],
    config: dict,
) -> Path:
    """Write the scoring contract next to the model so serving needs no assumptions.

    The threshold in particular must travel with the artefact: a probability is not a decision.
    """
    metadata = {
        "model_name": best_name,
        "threshold": threshold,
        "target": "is_late = order_delivered_customer_date > order_estimated_delivery_date",
        "prediction_timestamp": PURCHASE_COLUMN,
        "test_metrics": {k: round(v, 6) for k, v in test_metrics.items()},
        "split": config.get("split", {}),
        "cost": config.get("cost", {}),
    }
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    logger.info("wrote model metadata to %s", path)
    return path


def run_training(
    order_level: pd.DataFrame,
    config: dict | None = None,
    *,
    models: list[str] | None = None,
    tune: list[str] | None = None,
    track: bool = True,
) -> TrainingResult:
    """Run the full experiment and return every artefact the report needs."""
    config = config or load_model_config()
    seed = int(config.get("seed", SEED))
    windows = split_windows_from_config(config)
    cost = CostMatrix(**config.get("cost", {}))

    frame = build_modelling_frame(order_level)
    split = chronological_split(frame, windows)

    candidates = models or model_names(config)
    tune = (
        tune
        if tune is not None
        else list(config.get("tuning", {}).get("models", ["logistic_regression", "lightgbm"]))
    )

    # ---------------------------------------------------------------- baselines
    baseline_rows: dict[str, dict[str, float]] = {}
    fitted: dict[str, object] = {}
    for name in candidates:
        pipeline = build_model(name, config, seed=seed).fit(*_xy(split.train))
        fitted[name] = pipeline
        baseline_rows[name] = evaluate_pipeline(pipeline, split.validation, cost=cost)
        logger.info(
            "baseline %-20s val PR-AUC %.4f  ROC-AUC %.4f  recall %.3f @%.2f",
            name,
            baseline_rows[name]["pr_auc"],
            baseline_rows[name]["roc_auc"],
            baseline_rows[name]["recall"],
            baseline_rows[name]["threshold"],
        )

    # ------------------------------------------------------------------- tuning
    folds = temporal_cv_splits(
        split.train,
        n_splits=int(config.get("cv", {}).get("n_splits", 4)),
        purge_days=int(config.get("cv", {}).get("purge_days", 30)),
    )
    logger.info("temporal CV: %s folds over the training window", len(folds))

    tuned_rows: dict[str, dict[str, float]] = {}
    tuned_params: dict[str, dict] = {}
    for name in tune:
        params, cv_pr_auc = _tune(name, config, split.train, folds, cost=cost)
        tuned_params[name] = params
        tuned_config = {**config, "models": {**config.get("models", {}), name: params}}
        pipeline = build_model(name, tuned_config, seed=seed).fit(*_xy(split.train))
        fitted[f"{name} (tuned)"] = pipeline
        tuned_rows[name] = {
            **evaluate_pipeline(pipeline, split.validation, cost=cost),
            "cv_pr_auc": cv_pr_auc,
        }
        logger.info(
            "tuned %-20s val PR-AUC %.4f  ROC-AUC %.4f  recall %.3f @%.2f (CV %.4f)",
            name,
            tuned_rows[name]["pr_auc"],
            tuned_rows[name]["roc_auc"],
            tuned_rows[name]["recall"],
            tuned_rows[name]["threshold"],
            cv_pr_auc,
        )

    # ------------------------------------------- threshold on validation, then test
    ranked = {**baseline_rows, **{f"{k} (tuned)": v for k, v in tuned_rows.items()}}
    best_name = max(ranked, key=lambda k: ranked[k]["pr_auc"])
    best_pipeline = fitted[best_name]
    logger.info("selected %s on validation PR-AUC", best_name)

    validation_proba = _proba(best_pipeline, split.validation)
    threshold, curve = best_cost_threshold(
        split.validation[LABEL_COLUMN].to_numpy(), validation_proba, cost=cost
    )
    sensitivity = cost_sensitivity(split.validation[LABEL_COLUMN].to_numpy(), validation_proba)

    test_metrics = compute_metrics(
        split.test[LABEL_COLUMN].to_numpy(),
        _proba(best_pipeline, split.test),
        threshold=threshold,
        cost=cost,
    )
    logger.info(
        "TEST %s @%.2f: PR-AUC %.4f  ROC-AUC %.4f  precision %.3f  recall %.3f  cost/order %.2f",
        best_name,
        threshold,
        test_metrics["pr_auc"],
        test_metrics["roc_auc"],
        test_metrics["precision"],
        test_metrics["recall"],
        test_metrics["cost_per_order"],
    )

    importance = _feature_importance(best_pipeline)

    artifact_path = save_pipeline(best_pipeline)
    _write_metadata(
        path=artifact_path.with_suffix(".json"),
        threshold=threshold,
        best_name=best_name,
        test_metrics=test_metrics,
        config=config,
    )

    run_id = ""
    if track:
        input_example = _xy(split.train)[0].head(5)
        run_id = _log_to_mlflow(
            config=config,
            split_summary=split.summary,
            baseline=metrics_frame(baseline_rows),
            tuned=metrics_frame(tuned_rows),
            tuned_params=tuned_params,
            best_name=best_name,
            best_pipeline=best_pipeline,
            threshold=threshold,
            test_metrics=test_metrics,
            curve=curve,
            sensitivity=sensitivity,
            importance=importance,
            input_example=input_example,
        )

    return TrainingResult(
        config=config,
        split_summary=split.summary,
        baseline_results=metrics_frame(baseline_rows),
        tuned_results=metrics_frame(tuned_rows),
        test_metrics=test_metrics,
        threshold=threshold,
        cost_curve=curve,
        cost_sensitivity=sensitivity,
        best_model_name=best_name,
        run_id=run_id,
        feature_importance=importance,
    )


def _log_to_mlflow(
    *,
    config: dict,
    split_summary: pd.DataFrame,
    baseline: pd.DataFrame,
    tuned: pd.DataFrame,
    tuned_params: dict[str, dict],
    best_name: str,
    best_pipeline,
    threshold: float,
    test_metrics: dict[str, float],
    curve: pd.DataFrame,
    sensitivity: pd.DataFrame,
    importance: pd.DataFrame,
    input_example: pd.DataFrame,
) -> str:
    """Log one MLflow run: parameters, metrics, tables and the fitted pipeline."""
    tracking = config.get("mlflow", {})
    db_path = PROJECT_ROOT / tracking.get("tracking_db", "mlflow.db")
    mlflow.set_tracking_uri(f"sqlite:///{db_path.as_posix()}")
    mlflow.set_experiment(tracking.get("experiment", "delivery-delay"))

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    with mlflow.start_run(run_name=f"{best_name}·cost-threshold") as run:
        mlflow.log_params(
            {
                "seed": config.get("seed", SEED),
                "split.train_end": str(config["split"]["train_end"]),
                "split.validation_end": str(config["split"]["validation_end"]),
                "split.test_end": str(config["split"]["test_end"]),
                "cost.false_negative": config.get("cost", {}).get("false_negative", 10.0),
                "cost.false_positive": config.get("cost", {}).get("false_positive", 1.0),
                "cv.n_splits": config.get("cv", {}).get("n_splits", 4),
                "cv.purge_days": config.get("cv", {}).get("purge_days", 30),
                "selected_model": best_name,
                "threshold": threshold,
            }
        )
        for name, params in tuned_params.items():
            for key, value in params.items():
                mlflow.log_param(f"{name}.{key}", value)

        mlflow.log_metrics({f"test_{k}": v for k, v in test_metrics.items()})
        for partition, row in baseline.iterrows():
            for metric in ("pr_auc", "roc_auc", "precision", "recall", "f1"):
                mlflow.log_metric(f"val_{partition}_{metric}", float(row[metric]))
        for partition, row in tuned.iterrows():
            for metric in ("pr_auc", "roc_auc", "precision", "recall", "f1", "cv_pr_auc"):
                if metric in row and pd.notna(row[metric]):
                    mlflow.log_metric(f"val_{partition}_tuned_{metric}", float(row[metric]))

        baseline.to_csv(ARTIFACT_DIR / "validation_baselines.csv")
        tuned.to_csv(ARTIFACT_DIR / "validation_tuned.csv")
        split_summary.to_csv(ARTIFACT_DIR / "split_summary.csv", index=False)
        curve.to_csv(ARTIFACT_DIR / "threshold_curve.csv", index=False)
        sensitivity.to_csv(ARTIFACT_DIR / "cost_sensitivity.csv", index=False)
        importance.to_csv(ARTIFACT_DIR / "feature_importance.csv", index=False)

        for path in (
            "validation_baselines.csv",
            "validation_tuned.csv",
            "split_summary.csv",
            "threshold_curve.csv",
            "cost_sensitivity.csv",
            "feature_importance.csv",
        ):
            mlflow.log_artifact(str(ARTIFACT_DIR / path))

        registered_name = tracking.get("registered_model")
        signature = infer_signature(input_example, best_pipeline.predict_proba(input_example)[:, 1])
        log_sklearn_model(
            best_pipeline,
            name="model",
            registered_model_name=registered_name,
            input_example=input_example,
            signature=signature,
            # skops refuses to serialise the numpy dtypes inside the fitted column transformer
            # without an explicit trust list; pickle is the safer default for a local registry.
            serialization_format="pickle",
        )

        logger.info("mlflow run %s logged to %s", run.info.run_id, db_path)
        return run.info.run_id
