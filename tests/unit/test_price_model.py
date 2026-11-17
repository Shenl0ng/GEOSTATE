import datetime as dt

import numpy as np
import pandas as pd
import pytest

from geostate import price_model as pm
from geostate import segments as sg

Q1, Q2 = dt.date(2018, 1, 1), dt.date(2018, 4, 1)


def listings(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """Synthetic listings: price per m² rises towards the centre and with a lift."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            "asset_id": [f"A{i}" for i in range(n)],
            "period": [Q1 if i % 2 else Q2 for i in range(n)],
            "lat": 40.40 + rng.uniform(0, 0.05, n),
            "lon": -3.70 + rng.uniform(0, 0.05, n),
            "area_m2": rng.uniform(40, 150, n),
            "has_lift": rng.integers(0, 2, n).astype(bool),
        }
    )
    for col in pm.FEATURES:
        if col not in df:
            df[col] = 0.0
    df["dist_center_km"] = np.hypot(df["lat"] - 40.42, df["lon"] + 3.68) * 100
    df["price_m2"] = 6_000 - 400 * df["dist_center_km"] + 500 * df["has_lift"]
    df["h3_fine"] = (df["lat"] * 100).round().astype(str)
    df["h3_coarse"] = "all"
    return df


def test_time_split_tests_on_the_last_quarter_only():
    train, test, last = pm.time_split(listings())
    assert last == Q2
    assert set(train["period"]) == {Q1}
    assert set(test["period"]) == {Q2}


def test_baseline_uses_cell_median_then_falls_back():
    train = pd.DataFrame(
        {"h3_fine": ["a"] * 5 + ["b"], "h3_coarse": ["p"] * 6, "price_m2": [1, 2, 3, 4, 5, 100.0]}
    )
    test = pd.DataFrame({"h3_fine": ["a", "b", "zz"], "h3_coarse": ["p", "p", "other"]})
    # cell a has 5 rows -> its median; b has 1 -> parent p median; zz unknown -> city median
    assert pm.baseline_predict(train, test, min_n=5).tolist() == [3.0, 3.5, 3.5]


def test_error_metrics():
    m = pm.error_metrics(np.array([100.0, 100.0]), np.array([90.0, 125.0]))
    assert m["median_ape"] == pytest.approx(0.175)
    assert m["within_10pct"] == 0.5
    assert m["within_20pct"] == 0.5


def test_model_beats_baseline_and_drops_repeated_assets():
    df = listings()
    df.loc[df.index[-1], "asset_id"] = df["asset_id"].iloc[0]  # same flat in both quarters
    result = pm.evaluate(df, pm.ModelConfig(max_iter=100, importance_sample=200))
    assert result["repeated_assets_dropped"] == 1
    assert result["model"]["median_ape"] < result["baseline_h3"]["median_ape"]
    assert result["top_features"][0]["feature"] in {"dist_center_km", "lat", "lon"}


def test_segments_are_ordered_by_price():
    df = listings(600).assign(
        cad_construction_year=1970.0, has_parking=False, has_pool=False, is_new_build=0.0
    )
    result = sg.segment(df, sg.SegmentConfig(min_listings=3, k_min=2, k_max=3))
    prices = [s["median_price_m2"] for s in result["segments"]]
    assert prices == sorted(prices)
    assert sum(s["cells"] for s in result["segments"]) == result["cells_used"]
