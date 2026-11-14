"""Delivery delay prediction interface.

Scores an order against the running prediction service and shows the result next to the factors
that produced it. The interface talks HTTP rather than importing the model, so it demonstrates
the same contract an external caller would use and can be pointed at a deployment with
``--api-url``.

Run it with::

    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import streamlit as st
from src.config import PROCESSED_DIR

DEFAULT_API_URL = os.getenv("API_URL", "http://localhost:8000")
API_TIMEOUT = float(os.getenv("API_TIMEOUT_SECONDS", "30"))

DEFAULT_STATES = ["SP", "RJ", "MG", "RS", "PR", "SC", "BA", "DF", "ES", "GO", "PE", "CE"]
DEFAULT_CATEGORIES = [
    "bed_bath_table",
    "health_beauty",
    "sports_leisure",
    "furniture_decor",
    "computers_accessories",
    "housewares",
    "watches_gifts",
    "telephony",
    "garden_tools",
    "auto",
]

RISK_COLOURS = {"low": "#1a7f37", "medium": "#9a6700", "high": "#b42318"}


# ------------------------------------------------------------------------- API access
def get_json(base_url: str, path: str) -> dict | None:
    try:
        response = requests.get(f"{base_url.rstrip('/')}{path}", timeout=API_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def get_options() -> dict[str, list[str]]:
    """States and categories as they appear in the training data, with a fallback list.

    The lists come from the analytical dataset when it is mounted beside the app. When it is
    not — for example a container that only holds the model — the fallback keeps the form usable.
    """
    try:
        from src.serving.samples import reference_options

        return reference_options()
    except (FileNotFoundError, ImportError, OSError):
        return {"states": DEFAULT_STATES, "categories": DEFAULT_CATEGORIES}


def get_sample_orders(limit: int = 5) -> list[dict]:
    """A handful of real checkout payloads, used to prefill the form."""
    try:
        from src.serving.samples import sample_orders

        return sample_orders(limit)
    except (FileNotFoundError, ImportError, OSError):
        return []


def post_predict(base_url: str, payload: dict) -> dict:
    response = requests.post(f"{base_url.rstrip('/')}/predict", json=payload, timeout=API_TIMEOUT)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------- form state
def build_payload(
    *,
    purchase_date: date,
    purchase_hour: int,
    lead_days: int,
    customer_state: str,
    seller_state: str,
    category: str,
    items: int,
    distance_km: float,
    weight_g: float,
    revenue: float,
    freight: float,
    installments: int,
    seller_id: str | None,
) -> dict:
    """Translate the form into an order payload, deriving the promised date from the lead time."""
    purchase = datetime.combine(purchase_date, datetime.min.time()).replace(hour=purchase_hour)
    estimate = purchase_date + timedelta(days=lead_days)

    payload: dict = {
        "order_purchase_timestamp": purchase.isoformat(),
        "order_estimated_delivery_date": estimate.isoformat(),
        "customer_state": customer_state.upper(),
        "seller_state": seller_state.upper(),
        "product_category": category,
        "n_items": items,
        "n_sellers": 1,
        "n_products": items,
        "item_revenue": round(revenue, 2),
        "freight_value": round(freight, 2),
        "payment_value": round(revenue + freight, 2),
        "max_installments": installments,
        "product_weight_g": round(weight_g, 1),
    }
    if distance_km >= 0:
        payload["seller_customer_distance_km"] = round(distance_km, 1)
    if seller_id:
        payload["primary_seller_id"] = seller_id.strip()
    return payload


def band_badge(band: str) -> str:
    colour = RISK_COLOURS.get(band, "#57606a")
    return (
        f"<span style='background:{colour};color:#fff;padding:2px 10px;"
        f"border-radius:10px;font-size:0.85em;'>{band.upper()} RISK</span>"
    )


def contributions_frame(items: list[dict] | None) -> pd.DataFrame:
    """Explanation rows in a form that reads as business drivers rather than model internals."""
    if not items:
        return pd.DataFrame(columns=["Driver", "Contribution (log-odds)"])
    return pd.DataFrame(
        {
            "Driver": [row["feature"].replace("_", " ") for row in items],
            "Contribution (log-odds)": [round(row["shap_value"], 4) for row in items],
        }
    )


# ------------------------------------------------------------------------------- layout
def render_sidebar(base_url: str) -> str:
    st.sidebar.header("Model service")
    url = st.sidebar.text_input("API URL", value=base_url)

    health = get_json(url, "/health")
    if health is None:
        st.sidebar.error("Service unreachable")
        return url

    if health.get("model_loaded"):
        st.sidebar.success(f"Model loaded · {health['model_version']}")
    else:
        st.sidebar.warning("Service up, model not loaded")

    st.sidebar.caption(
        "Explanations available" if health.get("explanations_available") else "Explanations off"
    )

    info = get_json(url, "/model/info")
    if info:
        with st.sidebar.expander("Scoring contract"):
            st.write(f"**Model:** {info['model_name']}")
            st.write(f"**Threshold:** {info['threshold']:.2f}")
            st.write(f"**Features:** {info['n_features']}")
            metrics = info.get("test_metrics", {})
            if metrics:
                st.write(f"**PR-AUC:** {metrics.get('pr_auc', 0):.3f}")
                st.write(f"**Precision:** {metrics.get('precision', 0):.3f}")
                st.write(f"**Recall:** {metrics.get('recall', 0):.3f}")
            st.caption(
                "PR-AUC is the primary metric: positives are 8% of orders, so accuracy is not "
                "informative."
            )

    metrics = get_json(url, "/metrics")
    if metrics:
        with st.sidebar.expander("Service counters"):
            st.write(f"**Orders scored:** {metrics['orders_scored']:,}")
            st.write(f"**Flagged:** {metrics['flagged_rate']:.1%}")
            st.write(f"**Mean latency:** {metrics['mean_latency_ms']:.0f} ms")
    return url


def render_result(result: dict) -> None:
    probability = result["delay_probability"]
    band = result["risk_band"]

    left, right = st.columns([1, 1])
    with left:
        st.metric("Delay probability", f"{probability:.1%}")
        st.markdown(band_badge(band), unsafe_allow_html=True)
        st.caption(
            f"Flagged as late: **{'yes' if result['predicted_late'] else 'no'}** "
            f"at a threshold of {result['threshold']:.2f}"
        )
    with right:
        st.progress(min(max(probability, 0.0), 1.0))

    if not result.get("increases_risk") and not result.get("decreases_risk"):
        st.info("Run with explanations enabled to see which factors drove this score.")
        return

    st.subheader("What drove the score")
    late_col, ontime_col = st.columns(2)
    with late_col:
        st.markdown("**Pushes towards late**")
        frame = contributions_frame(result.get("increases_risk"))
        if frame.empty:
            st.caption("Nothing raised the risk above the baseline.")
        else:
            st.dataframe(frame, hide_index=True, width="stretch")
    with ontime_col:
        st.markdown("**Pushes towards on time**")
        frame = contributions_frame(result.get("decreases_risk"))
        if frame.empty:
            st.caption("Nothing lowered the risk below the baseline.")
        else:
            st.dataframe(frame, hide_index=True, width="stretch")

    st.caption(
        "Contributions are log-odds; they sum to the model's score. A positive value makes the "
        "order look later than the reference order, a negative one earlier."
    )


def render_monitoring() -> None:
    """Show the latest historical replay and its delayed-label summaries, when available."""
    report_root = PROCESSED_DIR / "monitoring"
    manifest_path = report_root / "latest-run.json"
    st.subheader("Monitoring")
    if not manifest_path.exists():
        st.info("No monitoring replay is available yet. Run `python -m src.monitoring run`.")
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        st.error(f"Could not read the monitoring summary: {exc}")
        return

    if not manifest.get("simulation"):
        st.error("The monitoring output is missing its simulation marker.")
        return
    st.warning(
        "SIMULATED — this is a replay of historical orders, not live production telemetry."
    )

    for batch in manifest.get("batches", []):
        batch_id = batch["batch_id"]
        batch_dir = report_root / "simulated" / batch_id
        with st.expander(f"{batch_id} · {batch['orders']:,} orders"):
            drift_path = batch_dir / "summary.json"
            performance_path = batch_dir / "performance.json"
            try:
                drift = json.loads(drift_path.read_text(encoding="utf-8"))
                performance = json.loads(performance_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                st.error(f"Could not read this batch's monitoring output: {exc}")
                continue

            first, second, third = st.columns(3)
            first.metric("Feature drift", len(drift["drifted_features"]))
            second.metric("Prediction drift", "Yes" if drift["prediction_drift"] else "No")
            third.metric("Labels available", f"{performance['labelled_orders']:,}")
            if performance["status"] == "ready":
                st.write(
                    f"**PR-AUC:** {performance['pr_auc']:.3f} · "
                    f"**late-rate baseline:** {performance['base_rate']:.1%} · "
                    f"**lift:** {performance['pr_auc_lift_over_base_rate']:.2f}x"
                )
                st.caption(
                    "PR-AUC is reported against the observed late-rate baseline; this label "
                    "tracks promise dates, not delivery performance."
                )
            else:
                st.info("Not enough matured labels to calculate classification performance.")

            if st.button("View interactive drift report", key=f"drift-{batch_id}"):
                html_path = batch_dir / "drift.html"
                try:
                    report_html = html_path.read_text(encoding="utf-8")
                except OSError as exc:
                    st.error(f"Could not read the Evidently report: {exc}")
                else:
                    st.components.v1.html(report_html, height=800, scrolling=True)


def main() -> None:
    st.set_page_config(page_title="Delivery Delay Prediction", page_icon="📦", layout="wide")

    st.title("Delivery delay prediction")
    st.caption(
        "Estimates whether an order will arrive after its promised date, using only information "
        "available at checkout."
    )

    base_url = render_sidebar(DEFAULT_API_URL)
    options = get_options()
    samples = get_sample_orders()

    if samples:
        with st.expander("Start from a real order"):
            st.caption(
                "These are actual checkout payloads from the dataset. Any of them can be loaded "
                "into the form below as a starting point."
            )
            choice = st.selectbox(
                "Sample order",
                options=range(len(samples)),
                format_func=lambda i: (
                    f"{samples[i].get('customer_state', '??')} → "
                    f"{samples[i].get('seller_state', '??')} · "
                    f"{samples[i].get('product_category', 'unknown')}"
                ),
                index=None,
                placeholder="Choose an order",
            )
            if choice is not None:
                st.json(samples[choice])

    with st.form("order"):
        st.subheader("Order")
        first, second, third = st.columns(3)
        with first:
            purchase_date = st.date_input("Purchase date", value=date(2018, 3, 14))
            purchase_hour = st.slider("Purchase hour", 0, 23, 14)
        with second:
            lead_days = st.slider(
                "Promised lead time (days)",
                1,
                90,
                23,
                help="The delivery date the customer was promised, minus the purchase date.",
            )
        with third:
            items = st.number_input("Number of items", min_value=1, max_value=30, value=1)

        st.subheader("Route")
        route_first, route_second, route_third = st.columns(3)
        with route_first:
            customer_state = st.selectbox("Customer state", options["states"], index=0)
        with route_second:
            seller_state = st.selectbox("Seller state", options["states"], index=0)
        with route_third:
            distance_km = st.number_input(
                "Seller to customer distance (km)",
                min_value=-1.0,
                value=480.0,
                step=10.0,
                help="Leave at -1 when unknown; the model imputes it.",
            )

        st.subheader("Basket")
        basket_first, basket_second, basket_third = st.columns(3)
        with basket_first:
            category = st.selectbox("Product category", options["categories"], index=0)
        with basket_second:
            revenue = st.number_input("Item value (BRL)", min_value=0.0, value=129.9, step=10.0)
            freight = st.number_input("Freight (BRL)", min_value=0.0, value=22.4, step=1.0)
        with basket_third:
            weight_g = st.number_input("Product weight (g)", min_value=0.0, value=800.0, step=100.0)
            installments = st.number_input("Installments", min_value=1, max_value=24, value=1)

        seller_id = st.text_input(
            "Seller id (optional)",
            help="Supplying it lets the model use the seller's completed-order record.",
        )
        explain = st.checkbox("Explain this prediction", value=True)

        submitted = st.form_submit_button("Predict")

    st.divider()

    if submitted:
        payload = build_payload(
            purchase_date=purchase_date,
            purchase_hour=purchase_hour,
            lead_days=lead_days,
            customer_state=customer_state,
            seller_state=seller_state,
            category=category,
            items=int(items),
            distance_km=float(distance_km),
            weight_g=float(weight_g),
            revenue=float(revenue),
            freight=float(freight),
            installments=int(installments),
            seller_id=seller_id,
        )
        try:
            response = post_predict(base_url, {"orders": [payload], "explain": explain})
        except requests.HTTPError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            st.error(f"The service rejected the request: {detail}")
            return
        except requests.RequestException as exc:
            st.error(f"Could not reach the prediction service: {exc}")
            return

        st.subheader("Prediction")
        render_result(response["predictions"][0])

        with st.expander("Request payload"):
            st.json(payload)
    else:
        st.info("Set the order details and press **Predict**.")

    st.divider()
    render_monitoring()


main()
