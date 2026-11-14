"""Scoring new orders with the trained delivery-delay model.

The saved artefact is the whole pipeline — imputer, encoder and estimator — so scoring does not
repeat any preprocessing logic. ``predict_orders`` accepts the same feature frame the training
code produced, which keeps the online path and the batch path identical.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from src.config import PROCESSED_DIR
from src.features.build_features import ALL_FEATURES

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH: Path = PROCESSED_DIR / "modeling" / "delivery_delay_pipeline.joblib"


def model_fingerprint(path: Path) -> str:
    """Return a short content-addressed model version."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


#: Risk bands used by the API and the dashboard. Chosen against the tuned threshold so that
#: "high" starts where the expected cost of missing the order exceeds the cost of an alarm.
RISK_BANDS: tuple[tuple[str, float], ...] = (
    ("low", 0.30),
    ("medium", 0.60),
    ("high", 1.01),
)


@dataclass(frozen=True)
class PredictionBatch:
    """Predictions for a set of orders, ready for the API or a CSV export."""

    frame: pd.DataFrame
    threshold: float
    model_name: str

    def to_records(self) -> list[dict]:
        return self.frame.to_dict("records")


def risk_band(probability: float, threshold: float | None = None) -> str:
    """Map a probability to ``low`` / ``medium`` / ``high``.

    With a threshold, the bands are anchored on the decision so the label and the flag never
    disagree: ``high`` is exactly "flagged as late", ``medium`` is the band just below it where
    a small change in inputs would flip the decision, and ``low`` is comfortably below.

    Without a threshold the fixed :data:`RISK_BANDS` cut-points are used, which is only
    appropriate for a calibrated model.
    """
    if threshold is not None:
        if probability >= threshold:
            return "high"
        if probability >= threshold * 0.5:
            return "medium"
        return "low"

    for label, upper in RISK_BANDS:
        if probability < upper:
            return label
    return "high"


def save_pipeline(pipeline, path: Path | None = None) -> Path:
    """Persist the fitted pipeline."""
    target = path or DEFAULT_MODEL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, target)
    logger.info("saved pipeline to %s", target)
    return target


def load_pipeline(path: Path | None = None):
    """Load a fitted pipeline."""
    source = path or DEFAULT_MODEL_PATH
    if not source.exists():
        raise FileNotFoundError(f"no trained pipeline at {source} — run the training step first")
    return joblib.load(source)


def load_metadata(path: Path | None = None) -> dict:
    """Read the scoring contract written next to the artefact (threshold, metrics, splits)."""
    source = (path or DEFAULT_MODEL_PATH).with_suffix(".json")
    if not source.exists():
        logger.warning("no metadata file at %s — falling back to a 0.5 threshold", source)
        return {}
    return json.loads(source.read_text(encoding="utf-8"))


def predict_orders(
    pipeline,
    features: pd.DataFrame,
    *,
    threshold: float,
    model_name: str = "delivery-delay",
) -> PredictionBatch:
    """Score a feature frame and attach the decision, the band and the model name."""
    missing = set(ALL_FEATURES) - set(features.columns)
    if missing:
        raise KeyError(f"feature frame missing columns: {sorted(missing)}")

    matrix = features[list(ALL_FEATURES)]
    probability = np.asarray(pipeline.predict_proba(matrix)[:, 1], dtype=float)

    out = pd.DataFrame(
        {
            "delay_probability": np.round(probability, 6),
            "predicted_late": (probability >= threshold).astype(bool),
            "risk_band": [risk_band(p, threshold) for p in probability],
            "model_name": model_name,
            "threshold": threshold,
        },
        index=features.index,
    )
    logger.info(
        "scored %s orders: %s above the %.2f threshold",
        f"{len(out):,}",
        f"{int(out['predicted_late'].sum()):,}",
        threshold,
    )
    return PredictionBatch(frame=out, threshold=threshold, model_name=model_name)
