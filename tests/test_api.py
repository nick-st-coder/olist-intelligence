"""Tests for the prediction service and the HTTP interface.

The service is exercised through :class:`fastapi.testclient.TestClient` against the artefact the
training step wrote, so the tests fail if the saved model and the serving code ever disagree.
When no artefact is present the module is skipped rather than failed — a fresh clone has no
model until the training step runs.
"""

from __future__ import annotations

import pandas as pd
import pytest
from api.main import _contributions, _risk_band, create_app
from fastapi.testclient import TestClient
from src.config import ORDER_LEVEL_PATH
from src.modeling.predict import DEFAULT_MODEL_PATH
from src.serving.service import DeliveryDelayService

pytestmark = pytest.mark.skipif(
    not DEFAULT_MODEL_PATH.exists(),
    reason="no trained artefact; run `python -m src.modeling train` first",
)


@pytest.fixture(scope="module")
def service() -> DeliveryDelayService:
    return DeliveryDelayService().load()


@pytest.fixture(scope="module")
def client(service: DeliveryDelayService) -> TestClient:
    with TestClient(create_app(service)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def sample_orders() -> list[dict]:
    """Five real purchase-time payloads, built from the analytical dataset.

    Only checkout-observable fields are copied across: the actual delivery date, status and
    review columns are never read, so the payload cannot accidentally encode the label.
    """
    if not ORDER_LEVEL_PATH.exists():
        pytest.skip("order-level dataset absent")

    orders = pd.read_parquet(ORDER_LEVEL_PATH)
    labelled = orders[orders["is_late"].notna()].head(5)
    fields = [
        "order_purchase_timestamp",
        "order_estimated_delivery_date",
        "customer_state",
        "seller_state",
        "product_category",
        "primary_seller_id",
        "item_revenue",
        "freight_value",
        "payment_value",
        "n_items",
        "n_sellers",
        "n_products",
        "max_installments",
        "product_weight_g",
    ]
    payload: list[dict] = []
    for record in labelled[fields].to_dict("records"):
        cleaned = {}
        for key, value in record.items():
            if value is None or (isinstance(value, float) and pd.isna(value)):
                continue
            if key in {"order_purchase_timestamp", "order_estimated_delivery_date"}:
                cleaned[key] = pd.Timestamp(value).isoformat()
            else:
                cleaned[key] = value.item() if hasattr(value, "item") else value
        payload.append(cleaned)
    return payload


# ------------------------------------------------------------------------ service layer
def test_service_reports_its_scoring_contract(service: DeliveryDelayService) -> None:
    info = service.model_info()
    assert info["model_name"]
    assert 0.0 < info["threshold"] < 1.0
    assert info["n_features"] == len(info["features"])
    assert info["history_rows"] > 0


def test_service_predictions_are_probabilities(
    service: DeliveryDelayService, sample_orders
) -> None:
    frame = pd.DataFrame(sample_orders)
    scored = service.predict(frame)

    assert len(scored) == len(sample_orders)
    assert scored["delay_probability"].between(0.0, 1.0).all()
    assert scored["predicted_late"].dtype == bool
    assert set(scored["risk_band"]) <= {"low", "medium", "high"}
    # The band and the flag must never contradict each other.
    assert (scored["risk_band"] == "high").equals(scored["predicted_late"])


def test_service_ignores_the_actual_delivery_date(
    service: DeliveryDelayService, sample_orders
) -> None:
    """The label is unavailable at checkout, so supplying it must not move the score."""
    frame = pd.DataFrame(sample_orders)
    baseline = service.predict(frame, explain=False)

    tampered = frame.copy()
    tampered["order_delivered_customer_date"] = pd.Timestamp("2030-01-01")
    tampered["order_status"] = "delivered"
    after = service.predict(tampered, explain=False)

    pd.testing.assert_series_equal(
        baseline["delay_probability"], after["delay_probability"], check_names=False
    )


def test_service_explains_in_both_directions(service: DeliveryDelayService) -> None:
    # A long promised lead time and a blank seller record: the model should have something to say.
    order = {
        "order_purchase_timestamp": "2018-02-12T14:30:00",
        "order_estimated_delivery_date": "2018-04-20",
        "customer_state": "RJ",
        "seller_state": "SP",
        "product_category": "furniture_decor",
        "n_items": 2,
        "item_revenue": 320.0,
        "freight_value": 41.5,
        "payment_value": 361.5,
    }
    scored = service.predict(pd.DataFrame([order]), explain=True)
    record = scored.iloc[0]

    contributions = list(record["increases_risk"]) + list(record["decreases_risk"])
    assert contributions, "expected at least one contributing feature"
    assert all(abs(item["shap_value"]) > 0 for item in contributions)
    assert all(item["feature"] for item in contributions)


def test_service_metrics_accumulate(service: DeliveryDelayService, sample_orders) -> None:
    before = service.metrics.snapshot()["orders_scored"]
    service.predict(pd.DataFrame(sample_orders))
    after = service.metrics.snapshot()
    assert after["orders_scored"] == before + len(sample_orders)


# ---------------------------------------------------------------------------- HTTP layer
def test_health_reports_a_loaded_model(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["model_version"]


def test_model_info_returns_the_contract(client: TestClient) -> None:
    response = client.get("/model/info")
    assert response.status_code == 200
    body = response.json()
    assert body["prediction_timestamp"] == "order_purchase_timestamp"
    assert body["test_metrics"]["pr_auc"] > 0
    assert body["trained_split"]["train_end"]


def test_predict_returns_one_prediction_per_order(client: TestClient, sample_orders) -> None:
    response = client.post("/predict", json={"orders": sample_orders})
    assert response.status_code == 200
    body = response.json()

    assert len(body["predictions"]) == len(sample_orders)
    assert body["flagged"] == sum(1 for p in body["predictions"] if p["predicted_late"])
    for prediction in body["predictions"]:
        assert 0.0 <= prediction["delay_probability"] <= 1.0
        assert prediction["risk_band"] in {"low", "medium", "high"}
        assert prediction["model_version"] == body["model_version"]


def test_predict_with_explanations_returns_contributions(client: TestClient) -> None:
    order = {
        "order_purchase_timestamp": "2018-03-01T09:00:00",
        "order_estimated_delivery_date": "2018-04-15",
        "customer_state": "BA",
        "seller_state": "SP",
        "n_items": 1,
        "item_revenue": 129.9,
        "freight_value": 22.4,
        "payment_value": 152.3,
    }
    response = client.post("/predict", json={"orders": [order], "explain": True})
    assert response.status_code == 200
    prediction = response.json()["predictions"][0]

    assert prediction["increases_risk"] is not None
    for contribution in prediction["increases_risk"] + prediction["decreases_risk"]:
        assert set(contribution) == {"feature", "shap_value"}


def test_predict_without_explanations_omits_contributions(
    client: TestClient, sample_orders
) -> None:
    response = client.post("/predict", json={"orders": sample_orders[:1]})
    prediction = response.json()["predictions"][0]
    assert prediction["increases_risk"] is None


def test_predict_rejects_an_unparseable_timestamp(client: TestClient) -> None:
    response = client.post(
        "/predict",
        json={
            "orders": [
                {
                    "order_purchase_timestamp": "not-a-date",
                    "order_estimated_delivery_date": "2018-04-15",
                }
            ]
        },
    )
    assert response.status_code == 422


def test_predict_rejects_an_unknown_field(client: TestClient) -> None:
    """``extra="forbid"`` stops a caller silently sending a post-purchase field."""
    response = client.post(
        "/predict",
        json={
            "orders": [
                {
                    "order_purchase_timestamp": "2018-01-02T10:00:00",
                    "order_estimated_delivery_date": "2018-02-01",
                    "order_delivered_customer_date": "2018-01-30T10:00:00",
                }
            ]
        },
    )
    assert response.status_code == 422
    assert "order_delivered_customer_date" in response.text


def test_predict_rejects_an_empty_batch(client: TestClient) -> None:
    response = client.post("/predict", json={"orders": []})
    assert response.status_code == 422


def test_predict_rejects_a_coordinate_outside_brazil(client: TestClient) -> None:
    response = client.post(
        "/predict",
        json={
            "orders": [
                {
                    "order_purchase_timestamp": "2018-01-02T10:00:00",
                    "order_estimated_delivery_date": "2018-02-01",
                    "customer_lat": 51.5,
                    "customer_lng": -0.12,
                }
            ]
        },
    )
    assert response.status_code == 422


def test_metrics_endpoint_reports_counters(client: TestClient, sample_orders) -> None:
    client.post("/predict", json={"orders": sample_orders})
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["orders_scored"] >= len(sample_orders)
    assert body["errors"] == 0
    assert body["uptime_seconds"] > 0


def test_predict_returns_503_when_no_model_is_loaded() -> None:
    """A container without an artefact should report unavailability, not a stack trace."""
    with TestClient(create_app(DeliveryDelayService())) as degraded:
        assert degraded.get("/health").json()["status"] == "degraded"
        response = degraded.post(
            "/predict",
            json={
                "orders": [
                    {
                        "order_purchase_timestamp": "2018-01-02T10:00:00",
                        "order_estimated_delivery_date": "2018-02-01",
                    }
                ]
            },
        )
        assert response.status_code == 503


# ------------------------------------------------------------------------ payload helpers
def test_risk_band_accepts_the_three_declared_bands() -> None:
    for band in ("low", "medium", "high"):
        assert _risk_band(band) == band


def test_risk_band_rejects_an_undeclared_band() -> None:
    """A model that started emitting a fourth band must fail loudly, not pass through."""
    with pytest.raises(ValueError, match="unexpected risk band"):
        _risk_band("critical")


def test_contributions_treats_a_missing_cell_as_no_explanation() -> None:
    """A DataFrame round-trip turns an absent explanation into NaN, not None."""
    assert _contributions(None) is None
    assert _contributions(float("nan")) is None
    assert _contributions([]) == []


def test_contributions_parses_a_contribution_list() -> None:
    parsed = _contributions([{"feature": "estimated_lead_days", "shap_value": 0.5}])
    assert parsed is not None
    assert parsed[0].feature == "estimated_lead_days"
    assert parsed[0].shap_value == 0.5
