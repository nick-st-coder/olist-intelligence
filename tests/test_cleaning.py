"""Tests for the cleaning and aggregation pipeline.

These are the Phase 3 quality gates. They assert the three things that silently break an
order-level dataset: row multiplication, a wrong grain, and label leakage.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.config import DATA_DIR, FORBIDDEN_FEATURES, RAW_FILES
from src.ingestion.extract import load_raw_tables
from src.preprocessing.aggregation import (
    aggregate_order_items,
    aggregate_order_payments,
    aggregate_reviews,
)
from src.preprocessing.cleaning import (
    add_delivery_label,
    build_order_level_dataset,
    parse_timestamps,
    select_model_features,
)
from src.preprocessing.geolocation import aggregate_geolocation, haversine_km
from src.validation.schemas import validate_order_level


@pytest.fixture(scope="module")
def tables() -> dict[str, pd.DataFrame]:
    if not all((DATA_DIR / path).is_file() for path in RAW_FILES.values()):
        pytest.skip("raw CSVs absent; download them from Kaggle into data/")
    return load_raw_tables()


@pytest.fixture(scope="module")
def order_level(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return build_order_level_dataset(tables)


# ------------------------------------------------------------------ grain
def test_order_level_has_exactly_one_row_per_order(order_level: pd.DataFrame, tables) -> None:
    assert len(order_level) == len(tables["orders"])
    assert order_level["order_id"].is_unique


def test_no_row_multiplication_from_one_to_many_joins(order_level: pd.DataFrame, tables) -> None:
    """The whole point of aggregating first: 112,650 items must not become 112,650 orders."""
    assert len(order_level) == 99_441
    assert len(tables["order_items"]) > len(order_level)


def test_aggregate_order_items_is_one_row_per_order(tables) -> None:
    agg = aggregate_order_items(tables["order_items"])
    assert agg["order_id"].is_unique
    assert len(agg) == tables["order_items"]["order_id"].nunique()


def test_aggregate_order_payments_is_one_row_per_order(tables) -> None:
    agg = aggregate_order_payments(tables["order_payments"])
    assert agg["order_id"].is_unique
    assert len(agg) == tables["order_payments"]["order_id"].nunique()


def test_aggregate_reviews_deduplicates_to_one_row_per_order(tables) -> None:
    agg = aggregate_reviews(tables["order_reviews"])
    assert agg["order_id"].is_unique
    assert len(agg) == tables["order_reviews"]["order_id"].nunique()
    # 547 orders had more than one review; the dedup must remove exactly those extra rows.
    assert len(agg) < len(tables["order_reviews"])


def test_aggregate_geolocation_is_one_row_per_prefix(tables) -> None:
    agg = aggregate_geolocation(tables["geolocation"])
    assert agg["geolocation_zip_code_prefix"].is_unique
    assert len(agg) < len(tables["geolocation"])


# ------------------------------------------------------------------ label
def test_label_eligibility_matches_phase_one(order_level: pd.DataFrame) -> None:
    """Regression guard: Phase 1 established 96,470 eligible and 7,826 late."""
    eligible = int(order_level["is_label_eligible"].sum())
    late = int(order_level.loc[order_level["is_label_eligible"], "is_late"].sum())

    assert eligible == 96_470
    assert late == 7_826
    assert round(late / eligible * 100, 2) == 8.11


def test_label_is_null_exactly_when_not_eligible(order_level: pd.DataFrame) -> None:
    assert (order_level["is_late"].notna() == order_level["is_label_eligible"]).all()


def test_late_label_agrees_with_raw_timestamps(order_level: pd.DataFrame) -> None:
    """Compared on timestamps, not delay_days — delay_days truncates sub-day delays to 0."""
    late = order_level[order_level["is_late"] == True]  # noqa: E712
    ontime = order_level[order_level["is_late"] == False]  # noqa: E712

    assert (late["order_delivered_customer_date"] > late["order_estimated_delivery_date"]).all()
    assert (
        ontime["order_delivered_customer_date"] <= ontime["order_estimated_delivery_date"]
    ).all()


def test_sub_day_late_orders_are_labelled_late(order_level: pd.DataFrame) -> None:
    """1,292 orders are late by minutes on the estimate day; delay_days truncates them to 0."""
    late = order_level[order_level["is_late"] == True]  # noqa: E712
    sub_day = late[late["delay_days"] == 0]

    assert len(sub_day) == 1_292
    assert (sub_day["delay_hours"] > 0).all()
    assert (sub_day["delay_hours"] < 24).all()


def test_non_delivered_orders_are_not_eligible(order_level: pd.DataFrame) -> None:
    not_delivered = order_level[order_level["order_status"] != "delivered"]
    assert not not_delivered["is_label_eligible"].any()


# ------------------------------------------------------------------ leakage
def test_model_features_exclude_every_forbidden_column(order_level: pd.DataFrame) -> None:
    features = select_model_features(order_level)
    leaked = FORBIDDEN_FEATURES & set(features.columns)
    assert not leaked, f"leakage: {sorted(leaked)}"


def test_model_features_exclude_identifiers(order_level: pd.DataFrame) -> None:
    features = select_model_features(order_level)
    for col in ("order_id", "customer_id", "customer_unique_id", "primary_seller_id"):
        assert col not in features.columns


def test_estimated_lead_days_is_known_at_purchase(order_level: pd.DataFrame) -> None:
    """The only temporal feature allowed: estimate - purchase, both known at purchase."""
    features = select_model_features(order_level)
    assert "estimated_lead_days" in features.columns
    assert "delivery_days" not in features.columns
    assert "delay_days" not in features.columns


# ------------------------------------------------------------------ schema
def test_order_level_passes_pandera_schema(order_level: pd.DataFrame) -> None:
    validate_order_level(order_level)


def test_schema_rejects_a_leaked_label(order_level: pd.DataFrame) -> None:
    """A row claiming late with an on-time timestamp must fail validation."""
    from pandera.errors import SchemaError

    broken = order_level.head(200).copy()
    idx = broken.index[broken["is_label_eligible"]][0]
    broken.loc[idx, "is_late"] = True
    broken.loc[idx, "order_delivered_customer_date"] = broken.loc[idx, "order_purchase_timestamp"]

    with pytest.raises(SchemaError):
        validate_order_level(broken)


# ------------------------------------------------------------------ geography
def test_distance_is_non_negative_and_bounded(order_level: pd.DataFrame) -> None:
    dist = order_level["seller_customer_distance_km"].dropna()
    assert (dist >= 0).all()
    # No two points in Brazil are more than ~5,000 km apart.
    assert dist.max() < 5_000


def test_distance_available_for_every_order_with_items(order_level: pd.DataFrame) -> None:
    """The state-centroid fallback must cover the 278 customer / 7 seller missing prefixes."""
    with_items = order_level[order_level["n_items"].notna()]
    assert with_items["seller_customer_distance_km"].notna().all()


def test_haversine_matches_a_known_distance() -> None:
    """São Paulo (-23.55, -46.63) to Rio de Janeiro (-22.91, -43.17) is ~361 km."""
    d = haversine_km(
        pd.Series([-23.55]), pd.Series([-46.63]), pd.Series([-22.91]), pd.Series([-43.17])
    )
    assert 350 < d.iloc[0] < 375


def test_geolocation_drops_coordinates_outside_brazil(tables) -> None:
    agg = aggregate_geolocation(tables["geolocation"])
    assert agg["lat"].between(-34.0, 5.5).all()
    assert agg["lng"].between(-74.0, -34.0).all()


# ------------------------------------------------------------------ timestamps
def test_parse_timestamps_produces_datetimes(tables) -> None:
    parsed = parse_timestamps(tables["orders"])
    for col in (
        "order_purchase_timestamp",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ):
        assert pd.api.types.is_datetime64_any_dtype(parsed[col])


def test_add_delivery_label_does_not_mutate_input(tables) -> None:
    orders = parse_timestamps(tables["orders"])
    before = orders.columns.tolist()
    add_delivery_label(orders)
    assert orders.columns.tolist() == before
