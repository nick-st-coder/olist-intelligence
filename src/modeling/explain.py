"""SHAP explainability for the delivery-delay model.

Tree ensembles are explained with ``TreeExplainer`` on the fitted booster, fed with the
transformed matrix the model actually sees. Contributions are mapped back from the one-hot and
imputed columns to the original feature names so the report speaks in business terms
("estimated lead time", not "numeric__estimated_lead_days"), and a per-order explanation is
produced in probability space so it can be shown next to the prediction.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import shap
from src.features.build_features import CATEGORICAL_FEATURES

logger = logging.getLogger(__name__)


def _transformed(pipeline, features: pd.DataFrame) -> pd.DataFrame:
    matrix = pipeline.named_steps["preprocess"].transform(features)
    names = list(pipeline.named_steps["preprocess"].get_feature_names_out())
    return pd.DataFrame(matrix, columns=names, index=features.index)


def _unwrap(estimator):
    """Peel calibration off an estimator so SHAP can read its coefficients.

    ``CalibratedClassifierCV`` is a wrapper, not a model: SHAP cannot parse it. Calibration is a
    monotone transform of the score, so the underlying estimator's contributions are the right
    explanation of the ranking — only the mapping to a probability changes.
    """
    if hasattr(estimator, "calibrated_classifiers_") and estimator.calibrated_classifiers_:
        return estimator.calibrated_classifiers_[0].estimator
    return estimator


#: Rows in the reference distribution handed to SHAP. The default masker subsamples to 100
#: anyway; being explicit avoids the warning and makes the sampling reproducible.
BACKGROUND_ROWS = 200


def _explainer(pipeline, background: pd.DataFrame, *, max_samples: int = 100):
    """Build an explainer over a **reference distribution**, not over the row being explained.

    ``background`` must be a sample of rows the model was fitted on. Passing the single row
    whose contribution is wanted makes every Shapley value exactly zero — the row is its own
    baseline, so nothing deviates from it. Linear models are the case where this fails silently.
    """
    model = _unwrap(pipeline.named_steps["model"])
    if hasattr(model, "feature_importances_"):
        return shap.TreeExplainer(model)
    masker = shap.maskers.Independent(background, max_samples=max_samples)
    return shap.LinearExplainer(model, masker)


def _background_matrix(
    pipeline,
    features: pd.DataFrame,
    row_index: int,
    background: pd.DataFrame | None,
    rows: int,
    seed: int,
) -> pd.DataFrame:
    """Transformed reference rows used as the SHAP baseline.

    An explicit ``background`` wins. Otherwise the pool is sampled from ``features`` with the
    explained row removed, which is only meaningful when the caller passed a multi-row frame.
    """
    if background is not None:
        return _transformed(pipeline, background)

    pool = features.drop(index=[row_index], errors="ignore")
    if pool.empty:
        raise ValueError(
            "a per-order explanation needs a reference distribution; pass background=<frame> "
            "with rows the model was fitted on"
        )
    return _transformed(pipeline, pool.sample(min(rows, len(pool)), random_state=seed))


def global_importance(
    pipeline,
    features: pd.DataFrame,
    *,
    max_rows: int = 2000,
    seed: int = 42,
) -> pd.DataFrame:
    """Mean absolute SHAP value per transformed column, largest first.

    Computed on a sample because SHAP on 70k rows is slow and the ranking is stable well before
    that. The sample is seeded so the table is reproducible.
    """
    sample = features.sample(min(max_rows, len(features)), random_state=seed)
    matrix = _transformed(pipeline, sample)
    explainer = _explainer(pipeline, matrix)

    values = explainer.shap_values(matrix)
    if isinstance(values, list):
        values = values[1]
    values = np.asarray(values)
    if values.ndim == 3:
        values = values[:, :, 1]

    frame = pd.DataFrame(
        {
            "feature": matrix.columns,
            "mean_abs_shap": np.abs(values).mean(axis=0),
        }
    )
    frame = frame.sort_values("mean_abs_shap", ascending=False, ignore_index=True)
    frame["share"] = frame["mean_abs_shap"] / frame["mean_abs_shap"].sum()
    logger.info("SHAP: top drivers are %s", ", ".join(frame["feature"].head(8)))
    return frame


def grouped_importance(importance: pd.DataFrame) -> pd.DataFrame:
    """Collapse one-hot columns back to their source feature.

    ``customer_state_SP`` and its 26 siblings become a single ``customer_state`` row whose
    contribution is the sum of the group. The source names are matched explicitly rather than
    by pattern, because a state code and a category name are indistinguishable by shape.
    """
    frame = importance.copy()
    frame["group"] = frame["feature"].map(source_feature)
    grouped = (
        frame.groupby("group", as_index=False)["mean_abs_shap"]
        .sum()
        .sort_values("mean_abs_shap", ascending=False, ignore_index=True)
    )
    grouped["share"] = grouped["mean_abs_shap"] / grouped["mean_abs_shap"].sum()
    return grouped


def source_feature(name: str) -> str:
    """Map a transformed column name back to the feature it came from."""
    stripped = name.split("__", 1)[-1]
    for categorical in CATEGORICAL_FEATURES:
        if stripped == categorical or stripped.startswith(f"{categorical}_"):
            return categorical
    return stripped


def row_contributions(
    pipeline,
    features: pd.DataFrame,
    row_index: int,
    *,
    background: pd.DataFrame | None = None,
    background_rows: int = BACKGROUND_ROWS,
    seed: int = 42,
) -> pd.DataFrame:
    """Every transformed column's contribution to one order's score, in log-odds.

    Contributions are additive: they plus the explainer's base value reconstruct the model's
    decision function for this row. That identity is what makes the numbers presentable.
    """
    sample = features.loc[[row_index]]
    matrix = _transformed(pipeline, sample)
    baseline = _background_matrix(pipeline, features, row_index, background, background_rows, seed)
    explainer = _explainer(pipeline, baseline, max_samples=len(baseline))

    values = explainer.shap_values(matrix)
    if isinstance(values, list):
        values = values[1]
    values = np.atleast_2d(np.asarray(values))
    if values.ndim == 3:
        values = values[:, :, 1]

    frame = pd.DataFrame(
        {
            "feature": list(matrix.columns),
            "value": matrix.iloc[0].to_numpy(),
            "shap_value": values[0],
        }
    )
    frame["source_feature"] = frame["feature"].map(source_feature)
    return frame


def explain_row(
    pipeline,
    features: pd.DataFrame,
    row_index: int,
    *,
    background: pd.DataFrame | None = None,
    background_rows: int = BACKGROUND_ROWS,
    seed: int = 42,
    top_k: int = 10,
) -> pd.DataFrame:
    """Per-order contribution table in log-odds, largest absolute first.

    Positive contributions push the order towards "late"; negative ones push it towards
    "on time".
    """
    frame = row_contributions(
        pipeline,
        features,
        row_index,
        background=background,
        background_rows=background_rows,
        seed=seed,
    )
    frame["abs_shap"] = frame["shap_value"].abs()
    return (
        frame.sort_values("abs_shap", ascending=False)
        .head(top_k)
        .drop(columns=["abs_shap", "source_feature"])
        .reset_index(drop=True)
    )


def _grouped_contributions(contributions: pd.DataFrame) -> pd.DataFrame:
    """Sum one-hot contributions back onto their source feature.

    ``customer_state_SP`` and its 26 siblings collapse into a single ``customer_state`` term, so
    the explanation names a business input rather than a one-hot level.
    """
    grouped = (
        contributions.groupby("source_feature", as_index=False)["shap_value"]
        .sum()
        .rename(columns={"source_feature": "feature"})
    )
    grouped["abs_shap"] = grouped["shap_value"].abs()
    return grouped.sort_values("abs_shap", ascending=False, ignore_index=True)


def decision_explanation(
    pipeline,
    features: pd.DataFrame,
    row_index: int,
    *,
    threshold: float,
    background: pd.DataFrame | None = None,
    background_rows: int = BACKGROUND_ROWS,
    seed: int = 42,
    top_k: int = 5,
) -> dict:
    """A single prediction with the few contributions that drove it, in business language."""
    probability = float(pipeline.predict_proba(features.loc[[row_index]])[:, 1][0])
    contributions = row_contributions(
        pipeline,
        features,
        row_index,
        background=background,
        background_rows=background_rows,
        seed=seed,
    )
    grouped = _grouped_contributions(contributions)
    pushing_late = grouped[grouped["shap_value"] > 0].head(top_k)
    pushing_ontime = grouped[grouped["shap_value"] < 0].head(top_k)

    return {
        "probability": probability,
        "threshold": threshold,
        "predicted_late": probability >= threshold,
        "increases_risk": pushing_late[["feature", "shap_value"]].round(6).to_dict("records"),
        "decreases_risk": pushing_ontime[["feature", "shap_value"]].round(6).to_dict("records"),
    }
