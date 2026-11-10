"""Preprocessing pipelines for the delivery-delay model.

Imputation, encoding and scaling live inside a :class:`~sklearn.pipeline.Pipeline` so they are
fitted on the training window only. Nothing here touches the label, and the fitted pipeline is
saved with the model so serving applies exactly the same transformation as training.
"""

from __future__ import annotations

import logging

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from src.features.build_features import BOOLEAN_FEATURES, CATEGORICAL_FEATURES, NUMERIC_FEATURES

logger = logging.getLogger(__name__)

#: Only these columns are scaled. Rich models split on raw values, so scaling them is pointless.
_SCALED_NUMERIC: tuple[str, ...] = tuple(
    c for c in NUMERIC_FEATURES if c in {"item_revenue", "freight_value", "payment_value"}
)


def build_preprocessor(
    *,
    scale_numeric: bool,
    features: tuple[str, ...] | list[str] | None = None,
    numeric_features: tuple[str, ...] = NUMERIC_FEATURES,
    boolean_features: tuple[str, ...] = BOOLEAN_FEATURES,
    categorical_features: tuple[str, ...] = CATEGORICAL_FEATURES,
) -> ColumnTransformer:
    """Build the column transformer shared by every model.

    ``scale_numeric`` is ``True`` for linear models, which need it, and ``False`` for tree
    ensembles, which do not — and where it would only slow training down.

    Missing numeric values are imputed with the training median (and a flag-free sentinel is
    avoided on purpose: the ``seller_has_history`` boolean already tells the model whether the
    historical features are real or imputed). Unseen categories at serving time map to zeros.
    """
    if features is not None:
        allowed = set(features)
        numeric_features = tuple(c for c in numeric_features if c in allowed)
        boolean_features = tuple(c for c in boolean_features if c in allowed)
        categorical_features = tuple(c for c in categorical_features if c in allowed)

    numeric_steps: list[tuple[str, object]] = [("impute", SimpleImputer(strategy="median"))]
    if scale_numeric:
        numeric_steps.append(("scale", StandardScaler()))

    numeric_pipeline = Pipeline(numeric_steps)

    transformers: list[tuple[str, object, list[str]]] = [
        ("numeric", numeric_pipeline, list(numeric_features)),
        (
            "boolean",
            Pipeline([("impute", SimpleImputer(strategy="most_frequent"))]),
            list(boolean_features),
        ),
        (
            "categorical",
            Pipeline(
                [
                    ("impute", SimpleImputer(strategy="constant", fill_value="unknown")),
                    (
                        "encode",
                        OneHotEncoder(
                            handle_unknown="ignore", min_frequency=50, sparse_output=False
                        ),
                    ),
                ]
            ),
            list(categorical_features),
        ),
    ]

    preprocessor = ColumnTransformer(
        transformers, remainder="drop", verbose_feature_names_out=False
    )
    return preprocessor


def fitted_feature_names(preprocessor: ColumnTransformer) -> list[str]:
    """Return the output column names of a fitted column transformer."""
    return list(preprocessor.get_feature_names_out())


def describe_transform(preprocessor: ColumnTransformer) -> pd.DataFrame:
    """How many input columns each block consumes and how many it produces.

    The categorical block expands one column into one column per level, which is the reason the
    fitted matrix is much wider than the feature list.
    """
    rows = []
    for name, _transformer, columns in preprocessor.transformers_:
        if name == "remainder":
            continue
        rows.append({"block": name, "inputs": len(columns)})
    frame = pd.DataFrame(rows)
    frame["outputs"] = len(preprocessor.get_feature_names_out())
    return frame
