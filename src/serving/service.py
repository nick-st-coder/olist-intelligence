"""The prediction service, independent of any web framework.

Both the HTTP layer and the dashboard call into this class, so there is exactly one place that
knows how an order becomes a probability. The model, its scoring contract and the seller
completion timeline are loaded once and reused, because the timeline build is the expensive step
and nothing in it changes between requests.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from src.config import ORDER_LEVEL_PATH
from src.features.build_features import ALL_FEATURES
from src.features.historical import build_completion_timeline
from src.modeling.explain import decision_explanation
from src.modeling.predict import (
    DEFAULT_MODEL_PATH,
    load_metadata,
    load_pipeline,
    predict_orders,
)
from src.serving.features import build_serving_features

logger = logging.getLogger(__name__)

#: Rows kept as the SHAP reference distribution. The explainer subsamples to 100 internally.
BACKGROUND_ROWS = 300

#: Fraction of the reference frame kept for explanations. 2,000 rows is enough for a stable
#: background and small enough to build in under a second.
BACKGROUND_SAMPLE = 2000


def _fingerprint(path: Path) -> str:
    """Content-addressed model version.

    A hash of the artefact is a version that cannot drift from the binary it names — unlike a
    hand-maintained string, which goes stale silently after a retrain.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


@dataclass
class ServiceMetrics:
    """In-process counters for ``GET /metrics``.

    Deliberately process-local: a real deployment would export to Cloud Monitoring, but the
    counters still answer "is this instance serving, and what is it predicting".
    """

    started_at: float = field(default_factory=time.time)
    requests: int = 0
    orders_scored: int = 0
    orders_flagged: int = 0
    errors: int = 0
    explanations: int = 0
    latency_ms_total: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, *, orders: int, flagged: int, latency_ms: float, explained: bool) -> None:
        with self._lock:
            self.requests += 1
            self.orders_scored += orders
            self.orders_flagged += flagged
            self.latency_ms_total += latency_ms
            if explained:
                self.explanations += 1

    def record_error(self) -> None:
        with self._lock:
            self.errors += 1

    def snapshot(self) -> dict:
        with self._lock:
            requests = self.requests
            return {
                "requests": requests,
                "orders_scored": self.orders_scored,
                "orders_flagged": self.orders_flagged,
                "flagged_rate": (
                    self.orders_flagged / self.orders_scored if self.orders_scored else 0.0
                ),
                "errors": self.errors,
                "explanations": self.explanations,
                "mean_latency_ms": (self.latency_ms_total / requests if requests else 0.0),
                "uptime_seconds": time.time() - self.started_at,
            }


class DeliveryDelayService:
    """Score orders and explain the scores.

    Instantiating the service is cheap; :meth:`load` does the I/O. That split lets the tests
    build a service against a small in-memory frame instead of the full dataset.
    """

    def __init__(
        self,
        *,
        model_path: Path | None = None,
        metadata_path: Path | None = None,
        order_level_path: Path | None = None,
    ) -> None:
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.metadata_path = metadata_path or self.model_path.with_suffix(".json")
        self.order_level_path = order_level_path or ORDER_LEVEL_PATH

        self.pipeline = None
        self.metadata: dict = {}
        self.version = "unknown"
        self.threshold = 0.5
        self.completion: pd.DataFrame = pd.DataFrame()
        self.background: pd.DataFrame = pd.DataFrame()
        self.metrics = ServiceMetrics()
        self.loaded_at: float | None = None

    # ------------------------------------------------------------------ lifecycle
    def load(self) -> DeliveryDelayService:
        """Load the artefact, its scoring contract and the seller completion timeline."""
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"no trained model at {self.model_path} — run `python -m src.modeling train` first"
            )

        self.pipeline = load_pipeline(self.model_path)
        self.metadata = load_metadata(self.metadata_path)
        self.threshold = float(self.metadata.get("threshold", 0.5))
        self.version = _fingerprint(self.model_path)

        self._load_history_context()

        self.loaded_at = time.time()
        logger.info(
            "service ready: model=%s threshold=%.2f version=%s history_rows=%s",
            self.metadata.get("model_name", "unknown"),
            self.threshold,
            self.version,
            f"{len(self.completion):,}",
        )
        return self

    def _load_history_context(self) -> None:
        """Build the seller completion timeline and the SHAP background from delivered history.

        Both are optional. Without the order-level dataset the service still scores orders — the
        seller history columns stay at their imputed values and explanations use an empty
        reference, which SHAP handles by falling back to the model's own baseline. Losing the
        dataset degrades quality, it does not take the service down.
        """
        if not self.order_level_path.exists():
            logger.warning(
                "no order-level dataset at %s — seller history and SHAP background unavailable",
                self.order_level_path,
            )
            return

        orders = pd.read_parquet(self.order_level_path)
        self.completion = build_completion_timeline(orders)

        labelled = orders[orders["is_late"].notna()]
        if len(labelled) > BACKGROUND_SAMPLE:
            labelled = labelled.sample(BACKGROUND_SAMPLE, random_state=42)

        try:
            self.background = build_serving_features(labelled, completion=self.completion)
        except Exception:  # pragma: no cover - defensive, the frame is the training one
            logger.exception("could not build the SHAP background; explanations will be disabled")
            self.background = pd.DataFrame()

    @property
    def is_ready(self) -> bool:
        return self.pipeline is not None

    @property
    def can_explain(self) -> bool:
        return not self.background.empty

    # -------------------------------------------------------------------- scoring
    def predict(self, requests: pd.DataFrame, *, explain: bool = False) -> pd.DataFrame:
        """Score a frame of orders and return one row of output per input row."""
        if not self.is_ready or self.pipeline is None:
            raise RuntimeError("service is not loaded — call load() first")
        if requests.empty:
            raise ValueError("no orders supplied")

        started = time.perf_counter()
        features = build_serving_features(requests, completion=self.completion)

        try:
            batch = predict_orders(
                self.pipeline,
                features,
                threshold=self.threshold,
                model_name=str(self.metadata.get("model_name", "delivery-delay")),
            )
        except Exception:
            self.metrics.record_error()
            raise

        output = batch.frame.copy()
        output["model_version"] = self.version

        if explain:
            output = self._attach_explanations(output, features)

        self.metrics.record(
            orders=len(output),
            flagged=int(output["predicted_late"].sum()),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            explained=explain,
        )
        return output

    def _attach_explanations(self, output: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        """Add the driving factors per order, grouped back onto business feature names."""
        if not self.can_explain:
            output["increases_risk"] = [[] for _ in range(len(output))]
            output["decreases_risk"] = [[] for _ in range(len(output))]
            return output

        increases: list[list[dict]] = []
        decreases: list[list[dict]] = []

        for position in range(len(features)):
            explanation = decision_explanation(
                self.pipeline,
                features,
                features.index[position],
                threshold=self.threshold,
                background=self.background,
                top_k=5,
            )
            increases.append(explanation["increases_risk"])
            decreases.append(explanation["decreases_risk"])

        output["increases_risk"] = increases
        output["decreases_risk"] = decreases
        return output

    # ------------------------------------------------------------------ reporting
    def model_info(self) -> dict:
        """The scoring contract, as the API and the dashboard both report it."""
        return {
            "model_name": self.metadata.get("model_name", "unknown"),
            "model_version": self.version,
            "threshold": self.threshold,
            "target": self.metadata.get("target", ""),
            "prediction_timestamp": self.metadata.get("prediction_timestamp", ""),
            "trained_split": self.metadata.get("split", {}),
            "cost_matrix": self.metadata.get("cost", {}),
            "test_metrics": self.metadata.get("test_metrics", {}),
            "n_features": len(ALL_FEATURES),
            "features": list(ALL_FEATURES),
            "history_rows": len(self.completion),
            "explanations_available": self.can_explain,
        }

    def predict_one(self, order: dict, *, explain: bool = False) -> dict:
        """Score a single order, returning a plain dictionary ready for JSON."""
        frame = self.predict(pd.DataFrame([order]), explain=explain)
        record = frame.iloc[0].to_dict()
        return _jsonable(record)


def _jsonable(record: dict) -> dict:
    """Convert numpy scalars to Python types so the payload serialises without a custom encoder."""
    out = {}
    for key, value in record.items():
        if isinstance(value, (np.bool_, np.integer)):
            out[key] = value.item()
        elif isinstance(value, np.floating):
            out[key] = float(value)
        else:
            out[key] = value
    return out


_service: DeliveryDelayService | None = None
_lock = threading.Lock()


def get_service(**kwargs) -> DeliveryDelayService:
    """Process-wide singleton, loaded on first use."""
    global _service
    with _lock:
        if _service is None:
            _service = DeliveryDelayService(**kwargs).load()
        return _service


def reset_service() -> None:
    """Drop the cached service. Used by the tests."""
    global _service
    with _lock:
        _service = None
