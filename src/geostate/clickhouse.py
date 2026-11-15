"""ClickHouse: columnar store for the listing history and time-series analytics.

PostGIS stays the source of truth for geometry (spatial joins, tiles). ClickHouse receives a
flat, already-enriched copy (zone and H3 assigned in PostGIS) for fast aggregations at scale.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import clickhouse_connect
import pandas as pd
from clickhouse_connect.driver.client import Client

from geostate.config import Settings
from geostate.db import connect, read_sql, split_statements

log = logging.getLogger(__name__)

TABLES_PARTITIONED_BY_CITY = ("listings", "zone_quarter_stats")

EXPORT_SQL = """
SELECT l.city, l.asset_id, l.period, l.zone_id, z.zone_name, l.rooms, l.area_m2, l.price,
       l.price_m2, l.price_m2_adj, l.h3_fine, l.h3_coarse,
       ST_X(ST_Transform(l.geom, 4326)) AS lon, ST_Y(ST_Transform(l.geom, 4326)) AS lat
FROM core.listings l
JOIN core.zones z ON z.city = l.city AND z.zone_id = l.zone_id
WHERE l.city = %s
"""


def client(settings: Settings, database: str | None = None) -> Client:
    return clickhouse_connect.get_client(
        host=settings.ch_host,
        port=settings.ch_port,
        username=settings.ch_user,
        password=settings.ch_password,
        database=database if database is not None else settings.ch_database,
    )


def init_schema(settings: Settings) -> None:
    with client(settings, database="default") as ch:
        ch.command(f"CREATE DATABASE IF NOT EXISTS {settings.ch_database}")
    with client(settings) as ch:
        for statement in split_statements(read_sql("clickhouse_schema.sql")):
            ch.command(statement)


def partition_filter(city: str) -> str:
    """LIKE pattern for the text form of a (city, yyyymm) partition: "('Madrid',201801)"."""
    if not re.fullmatch(r"[\w .-]+", city):
        raise ValueError(f"unsupported city name {city!r}")
    return "('" + city.replace("_", "\\_") + "',%"


def drop_city_partitions(ch: Client, city: str) -> int:
    """Idempotency: remove every partition of this city before re-inserting it.

    DROP PARTITION is a cheap metadata operation, unlike ALTER ... DELETE (a heavy mutation).
    The aggregated table is dropped too: a materialized view only reacts to inserts, so stale
    aggregates would otherwise survive a re-run and be double counted.
    """
    dropped = 0
    for table in TABLES_PARTITIONED_BY_CITY:
        rows = ch.query(
            "SELECT DISTINCT partition_id FROM system.parts "
            "WHERE database = currentDatabase() AND table = {table:String} AND active "
            "AND partition LIKE {pattern:String}",
            parameters={"table": table, "pattern": partition_filter(city)},
        ).result_rows
        for (partition_id,) in rows:
            ch.command(f"ALTER TABLE {table} DROP PARTITION ID '{partition_id}'")
            dropped += 1
    return dropped


def export_core(city: str, settings: Settings) -> pd.DataFrame:
    with connect(settings.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(EXPORT_SQL, (city,))
        columns = [d.name for d in cur.description or []]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def sync_city(city: str, settings: Settings) -> dict[str, Any]:
    """Copy the clean listings of a city from PostGIS to ClickHouse (full refresh per city)."""
    init_schema(settings)
    df = export_core(city, settings)
    if df.empty:
        raise ValueError(f"no clean listings for {city} in PostGIS; run the core step first")
    df["rooms"] = df["rooms"].astype("Int64").astype(object).where(df["rooms"].notna(), None)

    with client(settings) as ch:
        dropped = drop_city_partitions(ch, city)
        ch.insert_df("listings", df)
        stored = ch.query(
            "SELECT count() FROM listings WHERE city = {city:String}", parameters={"city": city}
        ).result_rows[0][0]
    if stored != len(df):
        raise RuntimeError(f"ClickHouse has {stored} rows for {city}, expected {len(df)}")
    log.info("synced %d listings of %s to ClickHouse", stored, city)
    return {"rows": int(stored), "partitions_replaced": dropped}


def trends(city: str, settings: Settings, min_n: int = 20) -> pd.DataFrame:
    with client(settings) as ch:
        return ch.query_df(
            read_sql("clickhouse_trends.sql"), parameters={"city": city, "min_n": min_n}
        )
