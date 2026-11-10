"""Model zoo for the delivery-delay problem.

Every estimator is wrapped in the same :class:`~sklearn.pipeline.Pipeline` shape —
preprocessor first, model second — so a single fit/predict path serves the baselines, the
ensembles and the tuned models, and the saved artefact needs no special-casing at serving time.
"""

from __future__ import annotations

import logging

from lightgbm import LGBMClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from src.modeling.preprocessing import build_preprocessor
from xgboost import XGBClassifier

logger = logging.getLogger(__name__)

SEED = 42

#: Negatives per positive in the labelled dataset (88,644 / 7,826 ≈ 11.3). Used as the positive
#: class weight for XGBoost, which has no ``is_unbalance`` switch.
POSITIVE_WEIGHT = 11.3


def _common_tree_kwargs(seed: int) -> dict:
    return {"random_state": seed, "n_jobs": -1}


def build_model(
    name: str,
    config: dict,
    *,
    seed: int = SEED,
    features: tuple[str, ...] | list[str] | None = None,
    calibrate: bool | None = None,
) -> Pipeline:
    """Build an unfitted pipeline for ``name``.

    Supported names: ``dummy``, ``logistic_regression``, ``random_forest``, ``lightgbm``,
    ``xgboost``. The linear model gets scaled inputs; every tree model uses raw values.
    ``features`` restricts the pipeline to a subset of the declared columns.

    ``calibrate`` wraps the estimator in isotonic calibration. Class weighting is what makes the
    imbalanced problem learnable, but it inflates every probability by roughly the inverse of the
    positive rate — the balanced model's mean prediction is 0.49 against an actual late rate of
    0.068. Calibration restores a probability that can be read as a probability, which the
    prediction service and the dashboard both need. Defaults to the ``calibration.enabled``
    setting in the config.
    """
    params = {**config.get("models", {}).get(name, {})}
    params.pop("_comment", None)

    if calibrate is None:
        calibrate = bool(config.get("calibration", {}).get("enabled", True))
    calibrate = calibrate and name != "dummy"

    needs_scaling = name == "logistic_regression"
    preprocessor = build_preprocessor(scale_numeric=needs_scaling, features=features)

    if name == "dummy":
        estimator = DummyClassifier(strategy="prior")
    elif name == "logistic_regression":
        estimator = LogisticRegression(
            solver="lbfgs",
            class_weight="balanced",
            random_state=seed,
            **params,
        )
    elif name == "random_forest":
        estimator = RandomForestClassifier(
            class_weight="balanced_subsample",
            **_common_tree_kwargs(seed),
            **params,
        )
    elif name == "lightgbm":
        estimator = LGBMClassifier(
            objective="binary",
            random_state=seed,
            n_jobs=-1,
            verbose=-1,
            # 8.1% positives. Without this the booster learns to predict near-zero everywhere
            # and only a handful of orders clear a 0.5 threshold.
            is_unbalance=True,
            **params,
        )
    elif name == "xgboost":
        estimator = XGBClassifier(
            objective="binary:logistic",
            tree_method="hist",
            eval_metric="aucpr",
            random_state=seed,
            n_jobs=-1,
            scale_pos_weight=POSITIVE_WEIGHT,
            **params,
        )
    else:
        raise ValueError(f"unknown model: {name}")

    if calibrate:
        estimator = CalibratedClassifierCV(
            estimator,
            method=str(config.get("calibration", {}).get("method", "isotonic")),
            cv=int(config.get("calibration", {}).get("cv", 3)),
        )

    logger.debug("built %s pipeline (calibrated=%s)", name, calibrate)
    return Pipeline([("preprocess", preprocessor), ("model", estimator)])


def model_names(config: dict) -> list[str]:
    """The model names declared in ``configs/model.yaml``, in the order they should be run."""
    order = ["dummy", "logistic_regression", "random_forest", "lightgbm", "xgboost"]
    declared = set(config.get("models", {}))
    return [name for name in order if name in declared or name == "dummy"]
