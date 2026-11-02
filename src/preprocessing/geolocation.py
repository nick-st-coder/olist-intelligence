"""Geolocation aggregation and distance features.

``geolocation`` is a sample of observed coordinates, not a lookup: 1,000,163 rows cover 19,015
ZIP prefixes (mean 52.6 rows per prefix) and 93.5% of prefixes hold more than one distinct
coordinate pair. It must be collapsed to one representative point per prefix before any join.

Coordinates outside Brazil's bounding box are dropped before aggregation — the raw table
contains latitudes up to 45.1 and longitudes down to -101.5, which are not in Brazil.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

#: Rough bounding box for Brazil. Coordinates outside it are treated as bad data.
BRAZIL_BOUNDS: dict[str, float] = {
    "lat_min": -34.0,
    "lat_max": 5.5,
    "lng_min": -74.0,
    "lng_max": -34.0,
}

EARTH_RADIUS_KM = 6371.0


def aggregate_geolocation(geo: pd.DataFrame) -> pd.DataFrame:
    """Collapse ``geolocation`` to one median coordinate per ZIP prefix.

    Median is used rather than mean because the table is a sample of observations and the
    median is robust to the occasional wild coordinate that survives the bounding-box filter.
    """
    required = {
        "geolocation_zip_code_prefix",
        "geolocation_lat",
        "geolocation_lng",
        "geolocation_state",
    }
    missing = required - set(geo.columns)
    if missing:
        raise KeyError(f"geolocation missing columns: {sorted(missing)}")

    in_bounds = geo[
        geo["geolocation_lat"].between(BRAZIL_BOUNDS["lat_min"], BRAZIL_BOUNDS["lat_max"])
        & geo["geolocation_lng"].between(BRAZIL_BOUNDS["lng_min"], BRAZIL_BOUNDS["lng_max"])
    ]
    dropped = len(geo) - len(in_bounds)

    agg = (
        in_bounds.groupby("geolocation_zip_code_prefix", as_index=False)
        .agg(
            lat=("geolocation_lat", "median"),
            lng=("geolocation_lng", "median"),
            state=("geolocation_state", lambda s: s.mode().iat[0] if not s.mode().empty else None),
            n_geo_rows=("geolocation_lat", "size"),
        )
        .reset_index(drop=True)
    )

    if agg["geolocation_zip_code_prefix"].duplicated().any():
        raise AssertionError("aggregate_geolocation produced duplicate prefixes")

    logger.info(
        "geolocation %s rows -> %s prefixes (%s rows dropped outside Brazil bounds)",
        f"{len(geo):,}",
        f"{len(agg):,}",
        f"{dropped:,}",
    )
    return agg


def state_centroids(geo_agg: pd.DataFrame) -> pd.DataFrame:
    """Median coordinate per state, used as a fallback for prefixes absent from ``geolocation``."""
    return (
        geo_agg.groupby("state", as_index=False)
        .agg(state_lat=("lat", "median"), state_lng=("lng", "median"))
        .reset_index(drop=True)
    )


def attach_coordinates(
    df: pd.DataFrame,
    geo_agg: pd.DataFrame,
    zip_col: str,
    prefix: str,
) -> pd.DataFrame:
    """Attach ``{prefix}_lat`` / ``{prefix}_lng`` to ``df``, falling back to the state centroid.

    The fallback matters: 278 customer rows and 7 seller rows reference ZIP prefixes that do not
    exist in ``geolocation`` at all. Without a fallback those rows would carry null coordinates
    and silently drop out of any distance computation.
    """
    centroids = state_centroids(geo_agg)
    state_col = f"{prefix}_state"

    out = df.merge(
        geo_agg[["geolocation_zip_code_prefix", "lat", "lng"]].rename(
            columns={"lat": f"{prefix}_lat", "lng": f"{prefix}_lng"}
        ),
        left_on=zip_col,
        right_on="geolocation_zip_code_prefix",
        how="left",
    ).drop(columns=["geolocation_zip_code_prefix"])

    if state_col in out.columns:
        out = out.merge(centroids, left_on=state_col, right_on="state", how="left").drop(
            columns=["state"]
        )
        missing_before = int(out[f"{prefix}_lat"].isna().sum())
        out[f"{prefix}_lat"] = out[f"{prefix}_lat"].fillna(out["state_lat"])
        out[f"{prefix}_lng"] = out[f"{prefix}_lng"].fillna(out["state_lng"])
        out = out.drop(columns=["state_lat", "state_lng"])
        logger.info(
            "%s: %s rows had no prefix match, filled from state centroid",
            prefix,
            f"{missing_before:,}",
        )

    return out


def haversine_km(lat1: pd.Series, lng1: pd.Series, lat2: pd.Series, lng2: pd.Series) -> pd.Series:
    """Great-circle distance in kilometres between two coordinate pairs."""
    lat1, lng1, lat2, lng2 = (np.radians(v.astype(float)) for v in (lat1, lng1, lat2, lng2))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))
