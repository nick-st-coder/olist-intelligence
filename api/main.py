"""HTTP interface to the delivery-delay model.

Four endpoints, all backed by :class:`src.serving.service.DeliveryDelayService`:

* ``GET  /health``     liveness plus whether the artefact is loaded
* ``POST /predict``    score one or more orders, optionally with per-order explanations
* ``GET  /model/info`` the scoring contract: model, threshold, split, test metrics
* ``GET  /metrics``    in-process request and prediction counters

The service is loaded during application start-up, not on the first request, so a container that
cannot find its model fails its readiness probe instead of returning a 500 to a customer.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Literal, cast

import pandas as pd
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from src.serving.service import DeliveryDelayService, get_service, reset_service

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("olist.api")


# --------------------------------------------------------------------------- schemas
class OrderRequest(BaseModel):
    """The facts an order has at checkout. Nothing here is observable after delivery.

    Only the purchase timestamp and the promised delivery date are mandatory; the model imputes
    what is absent. ``estimated_lead_days`` is derived from the two dates rather than accepted
    from the client, so it can never contradict them.
    """

    model_config = ConfigDict(extra="forbid")

    order_purchase_timestamp: str = Field(..., description="Checkout instant, ISO 8601")
    order_estimated_delivery_date: str = Field(..., description="Promised delivery date")

    customer_state: str | None = Field(None, description="Brazilian state code, e.g. SP")
    seller_state: str | None = Field(None, description="Brazilian state code, e.g. RJ")
    product_category: str | None = Field(None, description="Category in English")

    customer_lat: float | None = Field(None, ge=-34.0, le=5.5)
    customer_lng: float | None = Field(None, ge=-74.0, le=-34.0)
    seller_lat: float | None = Field(None, ge=-34.0, le=5.5)
    seller_lng: float | None = Field(None, ge=-74.0, le=-34.0)
    seller_customer_distance_km: float | None = Field(None, ge=0.0)
    primary_seller_id: str | None = Field(None, description="Used to look up seller history")

    n_items: int | None = Field(None, ge=0)
    n_sellers: int | None = Field(None, ge=0)
    n_products: int | None = Field(None, ge=0)
    item_revenue: float | None = Field(None, ge=0.0)
    freight_value: float | None = Field(None, ge=0.0)
    payment_value: float | None = Field(None, ge=0.0)
    max_installments: int | None = Field(None, ge=0)
    product_weight_g: float | None = Field(None, ge=0.0)

    @field_validator("order_purchase_timestamp", "order_estimated_delivery_date")
    @classmethod
    def _parseable_timestamp(cls, value: str) -> str:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            raise ValueError(f"not a valid timestamp: {value!r}")
        return value

    def to_frame_row(self) -> dict:
        """Drop unset optional fields so the service fills them with its documented defaults."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class PredictRequest(BaseModel):
    """One or many orders, with an opt-in flag for per-order explanations."""

    model_config = ConfigDict(extra="forbid")

    orders: list[OrderRequest] = Field(..., min_length=1, max_length=500)
    explain: bool = Field(
        False,
        description="Return the driving factors per order. Roughly 100 ms per order.",
    )


class Contribution(BaseModel):
    feature: str
    shap_value: float


#: The bands the model can emit. Declared once so the response schema and the narrowing helper
#: cannot disagree about the allowed values.
RiskBand = Literal["low", "medium", "high"]
RISK_BANDS: frozenset[str] = frozenset({"low", "medium", "high"})


def _risk_band(value: object) -> RiskBand:
    """Narrow a scored band to :data:`RiskBand`, rejecting anything unexpected.

    The band arrives as an untyped cell from a DataFrame record, so it has to be checked rather
    than asserted: a model that started emitting a fourth band should fail loudly here instead of
    producing a response the schema silently accepts.
    """
    band = str(value)
    if band not in RISK_BANDS:
        raise ValueError(f"unexpected risk band from the model: {band!r}")
    return cast(RiskBand, band)


class Prediction(BaseModel):
    delay_probability: float
    predicted_late: bool
    risk_band: RiskBand
    model_name: str
    model_version: str
    threshold: float
    increases_risk: list[Contribution] | None = None
    decreases_risk: list[Contribution] | None = None


class PredictResponse(BaseModel):
    predictions: list[Prediction]
    flagged: int
    model_version: str
    threshold: float


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model_loaded: bool
    model_version: str
    explanations_available: bool


class ModelInfoResponse(BaseModel):
    model_name: str
    model_version: str
    threshold: float
    target: str
    prediction_timestamp: str
    trained_split: dict
    cost_matrix: dict
    test_metrics: dict
    n_features: int
    features: list[str]
    history_rows: int
    explanations_available: bool


class MetricsResponse(BaseModel):
    requests: int
    orders_scored: int
    orders_flagged: int
    flagged_rate: float
    errors: int
    explanations: int
    mean_latency_ms: float
    uptime_seconds: float


# ------------------------------------------------------------------------ application
def create_app(service: DeliveryDelayService | None = None) -> FastAPI:
    """Build the application. ``service`` lets the tests inject a preloaded instance."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if service is not None:
            app.state.service = service
        else:
            try:
                app.state.service = get_service()
            except FileNotFoundError as exc:
                # Degraded rather than dead: /health reports 503 and every other route answers
                # with a clear message instead of a stack trace.
                logger.error("model unavailable at start-up: %s", exc)
                app.state.service = DeliveryDelayService()
        yield
        reset_service()

    app = FastAPI(
        title="Delivery Delay Prediction",
        version="1.0.0",
        summary="Estimate whether an Olist order will arrive after its promised date.",
        lifespan=lifespan,
    )

    def current_service() -> DeliveryDelayService:
        instance = getattr(app.state, "service", None)
        if instance is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="service not initialised"
            )
        return instance

    @app.get("/health", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        instance = current_service()
        return HealthResponse(
            status="ok" if instance.is_ready else "degraded",
            model_loaded=instance.is_ready,
            model_version=instance.version,
            explanations_available=instance.can_explain,
        )

    @app.post("/predict", response_model=PredictResponse, tags=["prediction"])
    def predict(payload: PredictRequest) -> PredictResponse:
        instance = current_service()
        if not instance.is_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="model not loaded — run the training step and restart",
            )

        frame = pd.DataFrame([order.to_frame_row() for order in payload.orders])
        try:
            scored = instance.predict(frame, explain=payload.explain)
        except KeyError as exc:
            # Missing required feature columns: the payload is well-formed JSON but cannot be
            # turned into a feature frame.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("scoring failed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="scoring failed"
            ) from exc

        predictions = []
        for record in scored.to_dict("records"):
            predictions.append(
                Prediction(
                    delay_probability=float(record["delay_probability"]),
                    predicted_late=bool(record["predicted_late"]),
                    risk_band=_risk_band(record["risk_band"]),
                    model_name=str(record["model_name"]),
                    model_version=str(record["model_version"]),
                    threshold=float(record["threshold"]),
                    increases_risk=_contributions(record.get("increases_risk")),
                    decreases_risk=_contributions(record.get("decreases_risk")),
                )
            )

        return PredictResponse(
            predictions=predictions,
            flagged=sum(1 for p in predictions if p.predicted_late),
            model_version=instance.version,
            threshold=instance.threshold,
        )

    @app.get("/model/info", response_model=ModelInfoResponse, tags=["operations"])
    def model_info() -> ModelInfoResponse:
        instance = current_service()
        return ModelInfoResponse(**instance.model_info())

    @app.get("/metrics", response_model=MetricsResponse, tags=["operations"])
    def metrics() -> MetricsResponse:
        return MetricsResponse(**current_service().metrics.snapshot())

    return app


def _contributions(raw: object) -> list[Contribution] | None:
    """Normalise an explanation cell taken from a DataFrame record.

    A record round-trip turns an absent explanation into ``NaN`` rather than ``None``, so both
    have to mean "no explanation". Anything that is not a sequence of mappings is treated the
    same way rather than raising: the prediction itself is still valid without its explanation.
    """
    if raw is None:
        return None
    if isinstance(raw, float) and pd.isna(raw):
        return None
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        return None

    return [
        Contribution(feature=str(item["feature"]), shap_value=float(item["shap_value"]))
        for item in raw
        if isinstance(item, Mapping)
    ]


app = create_app()


def main() -> None:
    """``python -m api.main`` — a convenience runner for local use."""
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
