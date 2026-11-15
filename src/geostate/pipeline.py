"""Pipeline steps. Each step is a plain function that reads its input from the database
(or the source) and writes its output to the database, so an orchestrator such as
Airflow can run them as separate, retryable tasks. ``run`` chains them locally.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable
from typing import Any

import h3

from geostate import clickhouse, extract, load, quality
from geostate.config import Settings
from geostate.db import connect, read_sql, run_sql_file
from geostate.spatial import assign_h3
from geostate.transform import CleaningRules, clean, dedupe, normalize, validate_schema

log = logging.getLogger(__name__)
Stats = dict[str, Any]


def init_db(settings: Settings) -> None:
    with connect(settings.pg_dsn) as conn, conn.transaction():
        conn.execute(read_sql("schema.sql"))  # no parameters: run as one script


def stage(city: str, settings: Settings) -> Stats:
    """Extract + validate + clean + dedupe + H3 index -> staging tables."""
    sales = extract.download_source(city, "Sale", settings.source_base_url, settings.data_dir)
    polygons = extract.download_source(
        city, "Polygons", settings.source_base_url, settings.data_dir
    )

    raw = extract.read_rda(sales)
    validate_schema(raw)
    listings = normalize(raw)
    kept, rejected = clean(listings, CleaningRules())
    deduped = dedupe(kept)
    indexed = assign_h3(deduped, settings.h3_fine_res, settings.h3_coarse_res)
    zones = extract.read_zones(polygons)

    with connect(settings.pg_dsn) as conn:
        load.load_zones(conn, city, zones)
        load.load_listings(conn, city, indexed)
        load.load_rejected(conn, city, rejected)

    return {
        "rows_raw": len(raw),
        "unique_assets": int(listings["asset_id"].nunique()),
        "rejected_by_rules": rejected["reason"].value_counts().to_dict(),
        "duplicates_removed": len(kept) - len(deduped),
        "staged": len(indexed),
        "zones": len(zones),
    }


def build_core(city: str, settings: Settings) -> Stats:
    """Spatial join + geo QA + time adjustment, gated by the geolocation check."""
    with connect(settings.pg_dsn) as conn:
        with conn.transaction():
            run_sql_file(conn, "core.sql", {"city": city, "snap_m": settings.zone_snap_m})
            stats = quality.check_geolocation(conn, city, settings)  # fails -> rollback
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), count(*) FILTER (WHERE zone_distance_m > 0) "
                "FROM core.listings WHERE city = %s",
                (city,),
            )
            stats["core_listings"], stats["snapped_into_gap"] = cur.fetchone()  # type: ignore[misc]
            cur.execute(
                "SELECT period::text, round(factor::numeric, 4)::float FROM core.time_index "
                "WHERE city = %s ORDER BY period",
                (city,),
            )
            stats["time_index"] = dict(cur.fetchall())
    return stats


def build_marts(city: str, settings: Settings) -> Stats:
    """Honeycomb + zone prices + quality gates, all in one transaction (atomic publish)."""
    with connect(settings.pg_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT h3_fine FROM core.listings WHERE city = %s "
                "UNION SELECT DISTINCT h3_coarse FROM core.listings WHERE city = %s",
                (city, city),
            )
            cells = [row[0] for row in cur.fetchall()]
        load.upsert_h3_cells(conn, ((c, h3.get_resolution(c)) for c in cells))

        params = {"city": city, "min_n": settings.min_listings_per_cell, "k": settings.shrinkage_k}
        with conn.transaction():
            run_sql_file(conn, "marts.sql", params)
            stats = quality.check_marts(conn, city, settings)  # fails -> rollback, old marts stay
            stats["neighbor_outliers"] = quality.flag_neighbor_outliers(conn, city, settings)
        stats["h3_res"] = {"fine": settings.h3_fine_res, "coarse": settings.h3_coarse_res}
    return stats


def record_run(city: str, started_at: dt.datetime, stats: Stats, settings: Settings) -> None:
    with connect(settings.pg_dsn) as conn, conn.transaction():
        conn.execute(
            "INSERT INTO ops.pipeline_runs (city, started_at, stats) VALUES (%s, %s, %s)",
            (city, started_at, json.dumps(stats, default=str)),
        )


def sync_clickhouse(city: str, settings: Settings) -> Stats:
    """Copy clean listings to ClickHouse for time-series analytics (full refresh per city)."""
    return clickhouse.sync_city(city, settings)


# Ordered steps. Each one is idempotent and can run as its own orchestrator task.
STEPS: dict[str, Callable[[str, Settings], Stats]] = {
    "stage": stage,
    "core": build_core,
    "marts": build_marts,
    "clickhouse": sync_clickhouse,
}


def run(city: str, settings: Settings) -> Stats:
    started_at = dt.datetime.now(dt.UTC)
    init_db(settings)
    stats: Stats = {"city": city}
    for name, step in STEPS.items():
        log.info("step %s: start", name)
        stats[name] = step(city, settings)
        log.info("step %s: done %s", name, stats[name])
    record_run(city, started_at, stats, settings)
    return stats
