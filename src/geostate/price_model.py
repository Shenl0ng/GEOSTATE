"""Price model: predict asking price per m² for a listing, and check it beats the honeycomb.

The question a valuation product has to answer is "what is this flat worth?". The honeycomb
already gives an answer: the median €/m² of the cell. A model is only worth running in
production if it beats that simple answer on data it has not seen, so the evaluation is:

* **Time-based split:** train on the first three quarters of 2018, test on the last one,
  the same way a strategy is backtested (develop on one period, check on a later one).
* **Unseen flats only:** a flat listed again in a later quarter would be a near-copy of a
  training row and flatter the score, so any asset already seen in training is dropped from
  the test set. (In idealista18 every asset sits in a single quarter, so the check finds 0,
  but it is kept: on live data re-listings are common.)
* **Baseline:** median €/m² of the listing's H3 r9 cell in training (r7, then city, when the
  cell has too few listings): the honeycomb used as a valuation.

Everything here is offline and deterministic: it reads the source file, reuses the
pipeline's cleaning rules and returns plain dictionaries, so it is easy to test and to rerun.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

from geostate.spatial import assign_h3
from geostate.transform import CleaningRules, clean, dedupe, normalize, validate_schema

# Extra source columns used by the model only (the pipeline keeps a smaller set).
EXTRA_COLUMNS: dict[str, str] = {
    "HASAIRCONDITIONING": "has_aircon",
    "HASBOXROOM": "has_boxroom",
    "HASWARDROBE": "has_wardrobe",
    "HASSWIMMINGPOOL": "has_pool",
    "HASDOORMAN": "has_doorman",
    "HASGARDEN": "has_garden",
    "ISDUPLEX": "is_duplex",
    "ISINTOPFLOOR": "is_top_floor",
    "HASNORTHORIENTATION": "faces_north",
    "HASSOUTHORIENTATION": "faces_south",
    "FLOORCLEAN": "floor",
    "FLATLOCATIONID": "flat_location",  # exterior / interior
    "AMENITYID": "amenity",
    "CADCONSTRUCTIONYEAR": "cad_construction_year",
    "CADMAXBUILDINGFLOOR": "cad_building_floors",
    "CADDWELLINGCOUNT": "cad_dwellings",
    "CADASTRALQUALITYID": "cad_quality",
    "BUILTTYPEID_1": "is_new_build",
    "BUILTTYPEID_2": "is_second_hand",
    "BUILTTYPEID_3": "needs_renovation",
    "DISTANCE_TO_CITY_CENTER": "dist_center_km",
    "DISTANCE_TO_CASTELLANA": "dist_castellana_km",
}

FEATURES: tuple[str, ...] = (
    "area_m2",
    "rooms",
    "baths",
    "has_lift",
    "has_terrace",
    "has_parking",
    "is_studio",
    "construction_year",
    "dist_metro_km",
    "lon",
    "lat",
    *EXTRA_COLUMNS.values(),
)

TARGET = "price_m2"


@dataclass(frozen=True)
class ModelConfig:
    min_cell_listings: int = 5  # baseline: below this, fall back to the parent cell
    max_iter: int = 400
    learning_rate: float = 0.06
    max_leaf_nodes: int = 63
    random_state: int = 7
    importance_sample: int = 5_000  # rows used for permutation importance (speed)


def build_dataset(raw: pd.DataFrame, fine_res: int = 9, coarse_res: int = 7) -> pd.DataFrame:
    """Raw source -> clean, de-duplicated listings with model features.

    Uses the pipeline's own ``normalize``/``clean``/``dedupe``, so the model sees exactly the
    rows the map is built from (same rules, same lowest-price-per-quarter choice).
    """
    validate_schema(raw)
    missing = sorted(set(EXTRA_COLUMNS) - set(raw.columns))
    if missing:
        raise ValueError(f"source is missing model columns: {missing}")
    base = normalize(raw)  # keeps row order, so extras align by position
    extras = raw[list(EXTRA_COLUMNS)].rename(columns=EXTRA_COLUMNS).reset_index(drop=True)
    df = pd.concat([base, extras], axis=1)
    kept, _ = clean(df, CleaningRules())
    return assign_h3(dedupe(kept), fine_res, coarse_res)


def time_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dt.date]:
    """Train on every quarter but the last one, test on the last one."""
    last = max(df["period"])
    return df[df["period"] < last].copy(), df[df["period"] == last].copy(), last


def to_matrix(df: pd.DataFrame) -> np.ndarray:
    return df[list(FEATURES)].astype("float64").to_numpy(na_value=np.nan)


def baseline_predict(train: pd.DataFrame, test: pd.DataFrame, min_n: int) -> np.ndarray:
    """Honeycomb as a valuation: training median €/m² of the H3 r9 cell, else r7, else city."""
    pred = pd.Series(np.nan, index=test.index)
    for col in ("h3_fine", "h3_coarse"):
        stats = train.groupby(col)[TARGET].agg(["median", "size"])
        medians = stats.loc[stats["size"] >= min_n, "median"]
        pred = pred.fillna(test[col].map(medians))
    return pred.fillna(train[TARGET].median()).to_numpy()


def error_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    """Percentage errors, the unit a valuation is judged in."""
    ape = np.abs(predicted - actual) / actual
    return {
        "median_bias": round(float(np.median(predicted / actual - 1)), 4),  # < 0: undervalues
        "median_ape": round(float(np.median(ape)), 4),
        "mean_ape": round(float(np.mean(ape)), 4),
        "within_10pct": round(float(np.mean(ape <= 0.10)), 4),
        "within_20pct": round(float(np.mean(ape <= 0.20)), 4),
    }


def fit(train: pd.DataFrame, cfg: ModelConfig) -> HistGradientBoostingRegressor:
    """Gradient boosting on log(€/m²): errors become relative, which suits prices."""
    model = HistGradientBoostingRegressor(
        max_iter=cfg.max_iter,
        learning_rate=cfg.learning_rate,
        max_leaf_nodes=cfg.max_leaf_nodes,
        random_state=cfg.random_state,
    )
    model.fit(to_matrix(train), np.log(train[TARGET].to_numpy()))
    return model


def predict(model: HistGradientBoostingRegressor, df: pd.DataFrame) -> np.ndarray:
    return np.exp(model.predict(to_matrix(df)))


def evaluate(df: pd.DataFrame, cfg: ModelConfig | None = None) -> dict[str, Any]:
    """Train, test on the last quarter and compare with the honeycomb baseline."""
    cfg = cfg or ModelConfig()
    train, test, test_quarter = time_split(df)
    if train.empty or test.empty:
        raise ValueError("need at least two quarters to evaluate")
    model = fit(train, cfg)

    seen = test["asset_id"].isin(set(train["asset_id"]))
    unseen = test[~seen]
    actual = unseen[TARGET].to_numpy()
    model_metrics = error_metrics(actual, predict(model, unseen))
    baseline_metrics = error_metrics(actual, baseline_predict(train, unseen, cfg.min_cell_listings))

    sample = unseen.sample(min(cfg.importance_sample, len(unseen)), random_state=cfg.random_state)
    importance = permutation_importance(
        model,
        to_matrix(sample),
        np.log(sample[TARGET].to_numpy()),
        n_repeats=3,
        random_state=cfg.random_state,
    )
    ranked = sorted(zip(FEATURES, importance.importances_mean, strict=True), key=lambda kv: -kv[1])
    return {
        "train_quarters": sorted(str(p) for p in train["period"].unique()),
        "test_quarter": str(test_quarter),
        "n_train": len(train),
        "repeated_assets_dropped": int(seen.sum()),
        "n_test": len(unseen),
        "model": model_metrics,
        "baseline_h3": baseline_metrics,
        "top_features": [{"feature": f, "importance": round(float(v), 4)} for f, v in ranked[:10]],
    }
