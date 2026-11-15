"""Pure transformations on listings (no I/O), so they are easy to unit test."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd


class DataQualityError(Exception):
    """Raised when data fails a blocking quality gate. The pipeline must stop."""


# Source column -> our column. Only what the products need.
RAW_COLUMNS: dict[str, str] = {
    "ASSETID": "asset_id",
    "PERIOD": "period_raw",
    "PRICE": "price",
    "CONSTRUCTEDAREA": "area_m2",
    "ROOMNUMBER": "rooms",
    "BATHNUMBER": "baths",
    "HASLIFT": "has_lift",
    "HASTERRACE": "has_terrace",
    "HASPARKINGSPACE": "has_parking",
    "ISSTUDIO": "is_studio",
    "CONSTRUCTIONYEAR": "construction_year",
    "DISTANCE_TO_METRO": "dist_metro_km",
    "LONGITUDE": "lon",
    "LATITUDE": "lat",
}
BOOL_COLUMNS = ("has_lift", "has_terrace", "has_parking", "is_studio")


@dataclass(frozen=True)
class CleaningRules:
    min_area_m2: float = 15
    max_area_m2: float = 1_000
    min_price_m2: float = 300
    max_price_m2: float = 30_000


def validate_schema(raw: pd.DataFrame) -> None:
    missing = sorted(set(RAW_COLUMNS) - set(raw.columns))
    if missing:
        raise DataQualityError(f"source is missing columns: {missing}")
    if raw.empty:
        raise DataQualityError("source is empty")


def period_to_quarter_start(period: int) -> dt.date:
    """``201806`` (YYYYMM, end of quarter) -> ``2018-04-01`` (first day of that quarter)."""
    year, month = divmod(int(period), 100)
    if not 1 <= month <= 12:
        raise ValueError(f"invalid period {period}")
    quarter = (month - 1) // 3
    return dt.date(year, 3 * quarter + 1, 1)


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Select, rename and type the columns. Recompute price per m² instead of trusting it."""
    df = raw[list(RAW_COLUMNS)].rename(columns=RAW_COLUMNS).copy()
    df["asset_id"] = df["asset_id"].astype(str)
    df["period"] = df["period_raw"].map(period_to_quarter_start)
    df = df.drop(columns="period_raw")
    for col in BOOL_COLUMNS:
        df[col] = df[col].astype("boolean")
    df["price"] = df["price"].astype(float)
    df["area_m2"] = df["area_m2"].astype(float)
    df["price_m2"] = df["price"] / df["area_m2"]
    return df.reset_index(drop=True)


def clean(df: pd.DataFrame, rules: CleaningRules) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split into (kept, rejected). Rejected rows keep the *first* reason that applied."""
    reason = pd.Series(pd.NA, index=df.index, dtype="string")

    def flag(mask: pd.Series, label: str) -> None:
        nonlocal reason
        reason = reason.mask(reason.isna() & mask.fillna(True), label)

    flag(df["lon"].isna() | df["lat"].isna(), "missing_coordinates")
    flag(~df["area_m2"].between(rules.min_area_m2, rules.max_area_m2), "area_out_of_range")
    flag(~df["price_m2"].between(rules.min_price_m2, rules.max_price_m2), "price_m2_out_of_range")

    kept = df[reason.isna()].reset_index(drop=True)
    rejected = df[reason.notna()].assign(reason=reason[reason.notna()]).reset_index(drop=True)
    return kept, rejected


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (asset, quarter).

    In idealista18 the same ASSETID appears several times in one quarter with slightly
    different prices and coordinates (re-listings, several agencies, price drops).
    We keep the lowest asking price: it is the closest to the final sale price and
    avoids inflating medians. The whole row is kept, so coordinates stay consistent.
    """
    return (
        df.sort_values(["asset_id", "period", "price"], kind="stable")
        .drop_duplicates(subset=["asset_id", "period"], keep="first")
        .reset_index(drop=True)
    )
