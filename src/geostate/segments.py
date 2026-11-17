"""Market segments: group H3 cells into a few types of neighbourhood with k-means.

A price map answers "how expensive is it here?". Segments answer "what kind of market is
this?": cells with the same price can be very different (new builds with pools on the edge
of the city vs. old flats without lift in the centre). Agencies and product teams can use
segments to compare like with like.

Each cell is described by the listings in it (price, size, age, amenities, distance to the
centre). Features are standardised so no single unit dominates, and the number of segments
is chosen by silhouette score within a small range, so the result stays explainable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

# cell feature -> (listing column, aggregation)
CELL_FEATURES: dict[str, tuple[str, str]] = {
    "median_price_m2": ("price_m2", "median"),
    "median_area_m2": ("area_m2", "median"),
    "median_build_year": ("cad_construction_year", "median"),
    "share_lift": ("has_lift", "mean"),
    "share_parking": ("has_parking", "mean"),
    "share_pool": ("has_pool", "mean"),
    "share_new_build": ("is_new_build", "mean"),
    "dist_center_km": ("dist_center_km", "median"),
}


@dataclass(frozen=True)
class SegmentConfig:
    min_listings: int = 10  # cells with fewer listings are too noisy to describe
    k_min: int = 3
    k_max: int = 7
    random_state: int = 7


def cell_profiles(
    listings: pd.DataFrame, min_listings: int, cell_col: str = "h3_fine"
) -> pd.DataFrame:
    """One row per cell with enough listings, one column per feature in ``CELL_FEATURES``."""
    numeric = listings.assign(
        **{col: listings[col].astype("float64") for col, _ in CELL_FEATURES.values()}
    )
    grouped = numeric.groupby(cell_col)
    profiles = pd.DataFrame(
        {name: grouped[col].agg(how) for name, (col, how) in CELL_FEATURES.items()}
    )
    profiles["n"] = grouped.size()
    return profiles[profiles["n"] >= min_listings].dropna()


def choose_k(x: Any, cfg: SegmentConfig) -> tuple[int, dict[int, float]]:
    scores = {}
    for k in range(cfg.k_min, cfg.k_max + 1):
        labels = KMeans(n_clusters=k, n_init=10, random_state=cfg.random_state).fit_predict(x)
        scores[k] = round(float(silhouette_score(x, labels)), 4)
    return max(scores, key=lambda k: scores[k]), scores


def segment(listings: pd.DataFrame, cfg: SegmentConfig | None = None) -> dict[str, Any]:
    """Cluster cells and describe each segment by the medians of its cells."""
    cfg = cfg or SegmentConfig()
    profiles = cell_profiles(listings, cfg.min_listings)
    features = list(CELL_FEATURES)
    x = StandardScaler().fit_transform(profiles[features])
    k, scores = choose_k(x, cfg)
    profiles["segment"] = KMeans(
        n_clusters=k, n_init=10, random_state=cfg.random_state
    ).fit_predict(x)

    # Number segments from cheapest to most expensive, so ids are stable and readable.
    order = profiles.groupby("segment")["median_price_m2"].median().sort_values().index
    profiles["segment"] = profiles["segment"].map({old: new for new, old in enumerate(order)})

    summary = profiles.groupby("segment").agg(
        cells=("n", "size"), listings=("n", "sum"), **{f: (f, "median") for f in features}
    )
    return {
        "cells_used": len(profiles),
        "listings_covered": int(profiles["n"].sum()),
        "k": k,
        "silhouette_by_k": scores,
        "segments": summary.round(3).reset_index().to_dict(orient="records"),
        "cell_segments": profiles["segment"].to_dict(),
    }
