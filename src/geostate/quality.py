"""Quality gates. Blocking gates raise DataQualityError; warnings are only logged and reported."""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from geostate.config import Settings
from geostate.spatial import neighbor_outliers
from geostate.transform import DataQualityError

log = logging.getLogger(__name__)


def _scalar(conn: psycopg.Connection, sql: str, params: tuple) -> float:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def check_geolocation(conn: psycopg.Connection, city: str, settings: Settings) -> dict[str, Any]:
    """Blocking: too many listings outside every zone means bad source data or wrong polygons."""
    staged = _scalar(conn, "SELECT count(*) FROM staging.listings WHERE city = %s", (city,))
    outside = _scalar(
        conn,
        "SELECT count(*) FROM staging.listings_rejected "
        "WHERE city = %s AND reason = 'outside_city'",
        (city,),
    )
    ratio = outside / staged if staged else 1.0
    if ratio > settings.max_outside_city_ratio:
        raise DataQualityError(
            f"{ratio:.1%} of listings fall outside {city} zones "
            f"(max {settings.max_outside_city_ratio:.0%})"
        )
    return {"outside_city": outside, "outside_city_ratio": round(ratio, 4)}


def check_marts(conn: psycopg.Connection, city: str, settings: Settings) -> dict[str, Any]:
    """Blocking: marts must be non-empty, plausible, and cover most neighbourhoods."""
    fine = _scalar(conn, "SELECT count(*) FROM marts.hex_fine WHERE city = %s", (city,))
    coarse = _scalar(conn, "SELECT count(*) FROM marts.hex_coarse WHERE city = %s", (city,))
    if fine == 0 or coarse == 0:
        raise DataQualityError(f"empty honeycomb for {city} (fine={fine}, coarse={coarse})")

    implausible = _scalar(
        conn,
        "SELECT count(*) FROM marts.hex_fine "
        "WHERE city = %s AND median_m2 NOT BETWEEN 300 AND 30000",
        (city,),
    )
    if implausible:
        raise DataQualityError(f"{implausible:.0f} fine cells with implausible €/m²")

    coverage = _scalar(
        conn,
        "SELECT avg((median_m2 IS NOT NULL)::int) FROM marts.zone_prices WHERE city = %s",
        (city,),
    )
    if coverage < settings.min_zone_coverage:
        raise DataQualityError(
            f"only {coverage:.0%} of zones have a published price "
            f"(min {settings.min_zone_coverage:.0%})"
        )
    return {"hex_fine": fine, "hex_coarse": coarse, "zone_coverage": round(coverage, 4)}


def flag_neighbor_outliers(conn: psycopg.Connection, city: str, settings: Settings) -> int:
    """Warning: mark fine cells whose price is far from their neighbours' for expert review.

    RealAdvisor's methodology does the same: atypical areas are compared with
    neighbouring areas before publication.

    The check uses the *raw* cell median, not the smoothed one: shrinkage towards the
    parent cell pulls anomalies back to normal and would hide exactly what we look for
    (on Madrid: 10 cells flagged on raw medians vs 0 on smoothed ones).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT h3, raw_median_m2 FROM marts.hex_fine WHERE city = %s", (city,))
        medians = {h: float(m) for h, m in cur.fetchall()}
        flagged = neighbor_outliers(
            medians, settings.outlier_ratio_low, settings.outlier_ratio_high
        )
        cur.execute(
            "UPDATE marts.hex_fine SET outlier_vs_neighbors = (h3 = ANY(%s)) WHERE city = %s",
            (list(flagged), city),
        )
    if flagged:
        log.warning("%d fine cells differ strongly from their neighbours", len(flagged))
    return len(flagged)
