import datetime as dt

import pandas as pd
import pytest

from geostate.transform import (
    RAW_COLUMNS,
    CleaningRules,
    DataQualityError,
    clean,
    dedupe,
    normalize,
    period_to_quarter_start,
    validate_schema,
)


def raw_row(**overrides):
    row = {
        "ASSETID": "A1", "PERIOD": 201803, "PRICE": 200_000.0, "CONSTRUCTEDAREA": 50,
        "ROOMNUMBER": 2, "BATHNUMBER": 1, "HASLIFT": 1, "HASTERRACE": 0, "HASPARKINGSPACE": 0,
        "ISSTUDIO": 0, "CONSTRUCTIONYEAR": 1970, "DISTANCE_TO_METRO": 0.3,
        "LONGITUDE": -3.70, "LATITUDE": 40.42,
    }  # fmt: skip
    row.update(overrides)
    return row


def raw_frame(*rows):
    df = pd.DataFrame(list(rows))
    for col in ("ROOMNUMBER", "BATHNUMBER", "HASLIFT", "HASTERRACE", "HASPARKINGSPACE", "ISSTUDIO"):
        df[col] = df[col].astype("Int32")
    return df


@pytest.mark.parametrize(
    ("period", "expected"),
    [(201803, dt.date(2018, 1, 1)), (201806, dt.date(2018, 4, 1)), (201812, dt.date(2018, 10, 1))],
)
def test_period_maps_to_first_day_of_quarter(period, expected):
    assert period_to_quarter_start(period) == expected


def test_invalid_period_is_rejected():
    with pytest.raises(ValueError):
        period_to_quarter_start(201813)


def test_validate_schema_reports_missing_columns():
    with pytest.raises(DataQualityError, match="PRICE"):
        validate_schema(raw_frame(raw_row()).drop(columns="PRICE"))


def test_validate_schema_accepts_source_columns():
    validate_schema(raw_frame(raw_row()))
    assert set(RAW_COLUMNS) <= set(raw_frame(raw_row()).columns)


def test_normalize_recomputes_price_per_m2_and_types_booleans():
    df = normalize(raw_frame(raw_row(PRICE=300_000.0, CONSTRUCTEDAREA=100)))
    assert df.loc[0, "price_m2"] == 3_000
    assert df.loc[0, "has_lift"] is True or bool(df.loc[0, "has_lift"]) is True
    assert df.loc[0, "period"] == dt.date(2018, 1, 1)


def test_clean_rejects_with_first_matching_reason():
    df = normalize(
        raw_frame(
            raw_row(ASSETID="ok"),
            raw_row(ASSETID="no_coords", LONGITUDE=None, LATITUDE=None),
            raw_row(ASSETID="tiny", CONSTRUCTEDAREA=5),
            raw_row(ASSETID="cheap", PRICE=5_000.0),
        )
    )
    kept, rejected = clean(df, CleaningRules())
    assert kept["asset_id"].tolist() == ["ok"]
    assert dict(zip(rejected["asset_id"], rejected["reason"], strict=True)) == {
        "no_coords": "missing_coordinates",
        "tiny": "area_out_of_range",
        "cheap": "price_m2_out_of_range",
    }


def test_dedupe_keeps_lowest_price_per_asset_and_quarter():
    df = normalize(
        raw_frame(
            raw_row(ASSETID="A", PRICE=230_000.0, LATITUDE=40.4165),
            raw_row(ASSETID="A", PRICE=210_000.0, LATITUDE=40.4161),
            raw_row(ASSETID="A", PRICE=215_000.0),
            raw_row(ASSETID="A", PERIOD=201806, PRICE=220_000.0),  # other quarter: kept
        )
    )
    out = dedupe(df)
    assert len(out) == 2
    q1 = out[out["period"] == dt.date(2018, 1, 1)].iloc[0]
    assert q1["price"] == 210_000
    assert q1["lat"] == 40.4161  # the whole row of the kept listing, not a mix
