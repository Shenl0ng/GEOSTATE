"""Load: write DataFrames to PostGIS staging tables with COPY. Idempotent per city."""

from __future__ import annotations

import logging
from collections.abc import Iterable

import pandas as pd
import psycopg

from geostate.db import copy_rows, frame_rows
from geostate.spatial import cell_polygon_ewkt

log = logging.getLogger(__name__)

STAGING_COLUMNS = (
    "city", "asset_id", "period", "price", "area_m2", "price_m2", "rooms", "baths",
    "has_lift", "has_terrace", "has_parking", "is_studio", "construction_year",
    "dist_metro_km", "lon", "lat", "h3_fine", "h3_coarse",
)  # fmt: skip
REJECTED_COLUMNS = ("city", "asset_id", "period", "reason", "lon", "lat")


def load_listings(conn: psycopg.Connection, city: str, listings: pd.DataFrame) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM staging.listings WHERE city = %s", (city,))
        n = copy_rows(conn, "staging.listings", STAGING_COLUMNS,
                      frame_rows(listings.assign(city=city), STAGING_COLUMNS))  # fmt: skip
    log.info("staged %d listings", n)
    return n


def load_rejected(conn: psycopg.Connection, city: str, rejected: pd.DataFrame) -> int:
    """Store rejected rows with their reason: rejections must be auditable, not silent."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM staging.listings_rejected WHERE city = %s", (city,))
        return copy_rows(conn, "staging.listings_rejected", REJECTED_COLUMNS,
                         frame_rows(rejected.assign(city=city), REJECTED_COLUMNS))  # fmt: skip


def load_zones(conn: psycopg.Connection, city: str, zones: pd.DataFrame) -> int:
    rows = ((city, r.zone_id, r.zone_name, r.ewkt) for r in zones.itertuples(index=False))
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM staging.zones WHERE city = %s", (city,))
        return copy_rows(conn, "staging.zones", ("city", "zone_id", "zone_name", "geom"), rows)


def upsert_h3_cells(conn: psycopg.Connection, cells: Iterable[tuple[str, int]]) -> int:
    """Store polygons of H3 cells that are not stored yet (cells are global, shared by cities)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE tmp_cells (LIKE core.h3_cells) ON COMMIT DROP")
        copy_rows(conn, "tmp_cells", ("h3", "res", "geom"),
                  ((cell, res, cell_polygon_ewkt(cell)) for cell, res in cells))  # fmt: skip
        cur.execute("INSERT INTO core.h3_cells SELECT * FROM tmp_cells ON CONFLICT (h3) DO NOTHING")
        return cur.rowcount
