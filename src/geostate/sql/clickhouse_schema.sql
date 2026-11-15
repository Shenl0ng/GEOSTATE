-- ClickHouse schema (idempotent). Database name comes from settings and is set on the client.

-- One row per clean listing and quarter, copied from PostGIS core.
-- PARTITION BY (city, month): partitions are for *managing* data (drop/replace one city-quarter
-- on re-runs), not for speed. ORDER BY is the sparse primary index: most-filtered columns first.
CREATE TABLE IF NOT EXISTS listings
(
    city         LowCardinality(String),
    asset_id     String,
    period       Date,
    zone_id      LowCardinality(String),
    zone_name    LowCardinality(String),
    rooms        Nullable(UInt8),
    area_m2      Float32,
    price        Float64,
    price_m2     Float32,
    price_m2_adj Float32,
    h3_fine      String,
    h3_coarse    String,
    lon          Float64,
    lat          Float64
)
ENGINE = MergeTree
PARTITION BY (city, toYYYYMM(period))
ORDER BY (city, zone_id, period, asset_id);

-- Pre-aggregated stats per zone and quarter, maintained on every insert by the view below.
-- Stores aggregate *states*; read them with countMerge / quantileMerge.
CREATE TABLE IF NOT EXISTS zone_quarter_stats
(
    city      LowCardinality(String),
    zone_id   LowCardinality(String),
    zone_name LowCardinality(String),
    period    Date,
    n         AggregateFunction(count),
    median_m2 AggregateFunction(quantile(0.5), Float32)
)
ENGINE = AggregatingMergeTree
PARTITION BY (city, toYYYYMM(period))
ORDER BY (city, zone_id, zone_name, period);

-- Raw (not time-adjusted) price per m²: this table exists to measure change over time.
CREATE MATERIALIZED VIEW IF NOT EXISTS zone_quarter_stats_mv TO zone_quarter_stats AS
SELECT city, zone_id, zone_name, period,
       countState() AS n,
       quantileState(0.5)(price_m2) AS median_m2
FROM listings
GROUP BY city, zone_id, zone_name, period;
