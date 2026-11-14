"""Historical replay helpers for simulated production monitoring."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pandas as pd
from src.features.build_features import ALL_FEATURES, build_modelling_frame
from src.modeling.predict import predict_orders


@dataclass(frozen=True)
class SimulationCycle:
    """One monthly input batch and the labels available at its delayed evaluation time."""

    batch_id: str
    evaluated_at: pd.Timestamp
    batch: pd.DataFrame
    matured: pd.DataFrame


def build_simulated_stream(
    order_level: pd.DataFrame,
    pipeline,
    *,
    threshold: float,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    label_delay_days: int = 30,
) -> pd.DataFrame:
    """Score the requested historical window and attach its simulated label-arrival schedule.

    The label becomes observable only after delivery and the configured reporting delay.
    Purchases and features remain in their original chronological order; the static source data
    is used only to replay the outcomes that would eventually arrive.
    """
    if label_delay_days < 0:
        raise ValueError("label_delay_days cannot be negative")
    required = {
        "order_id",
        "order_purchase_timestamp",
        "order_delivered_customer_date",
        "is_late",
    }
    missing = required - set(order_level.columns)
    if missing:
        raise KeyError(f"order-level data is missing simulation columns: {sorted(missing)}")

    start_ts = pd.Timestamp(start)
    end_exclusive = pd.Timestamp(end).normalize() + pd.Timedelta(1, unit="D")
    frame = build_modelling_frame(order_level)
    purchase = pd.to_datetime(frame["order_purchase_timestamp"])
    mask = (purchase >= start_ts) & (purchase < end_exclusive)
    frame = frame.loc[mask].copy()
    if frame.empty:
        raise ValueError(f"no labelled orders in simulated window {start_ts.date()}..{end_exclusive.date()}")

    predictions = predict_orders(
        pipeline,
        frame.loc[:, list(ALL_FEATURES)],
        threshold=threshold,
        model_name="simulated-production",
    ).frame
    stream = frame[
        ["order_id", "order_purchase_timestamp", "is_late", *ALL_FEATURES]
    ].copy()
    stream["delay_probability"] = predictions["delay_probability"]
    stream["predicted_late"] = predictions["predicted_late"]
    stream["batch_id"] = pd.to_datetime(stream["order_purchase_timestamp"]).dt.to_period("M").astype(str)
    stream["batch_end"] = (
        pd.to_datetime(stream["order_purchase_timestamp"])
        .dt.to_period("M")
        .dt.end_time
    )
    delivered = pd.to_datetime(
        order_level.loc[stream.index, "order_delivered_customer_date"]
    )
    stream["label_available_at"] = delivered + pd.Timedelta(label_delay_days, unit="D")
    stream["simulated"] = True
    return stream.reset_index(drop=True)


def simulation_cycles(stream: pd.DataFrame, *, label_delay_days: int = 30) -> Iterator[SimulationCycle]:
    """Yield monthly batches and all outcomes observable by each batch's delayed review date."""
    required = {"batch_id", "batch_end", "label_available_at", "is_late"}
    missing = required - set(stream.columns)
    if missing:
        raise KeyError(f"simulation stream is missing columns: {sorted(missing)}")
    if label_delay_days < 0:
        raise ValueError("label_delay_days cannot be negative")

    ordered_batches = sorted(stream["batch_id"].dropna().unique())
    for batch_id in ordered_batches:
        batch = stream.loc[stream["batch_id"] == batch_id].copy()
        batch_end = pd.to_datetime(batch["batch_end"]).max()
        evaluated_at = batch_end + pd.Timedelta(label_delay_days, unit="D")
        matured = stream.loc[pd.to_datetime(stream["label_available_at"]) <= evaluated_at].copy()
        yield SimulationCycle(
            batch_id=str(batch_id),
            evaluated_at=pd.Timestamp(evaluated_at),
            batch=batch,
            matured=matured,
        )
