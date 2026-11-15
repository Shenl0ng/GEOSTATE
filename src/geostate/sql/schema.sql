-- GeoEstate schema. Idempotent: safe to run many times.
-- Layers: staging (raw, as loaded) -> core (clean, one row per listing+quarter) -> marts (served).
-- CRS: storage/compute in EPSG:25830 (ETRS89 / UTM 30N, metric, official for mainland Spain);
--      marts in EPSG:4326, the tile server reprojects to EPSG:3857.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS ops;

-- ---------------------------------------------------------------- staging
CREATE TABLE IF NOT EXISTS staging.listings (
    city              text             NOT NULL,
    asset_id          text             NOT NULL,
    period            date             NOT NULL,
    price             double precision NOT NULL,
    area_m2           double precision NOT NULL,
    price_m2          double precision NOT NULL,
    rooms             smallint,
    baths             smallint,
    has_lift          boolean,
    has_terrace       boolean,
    has_parking       boolean,
    is_studio         boolean,
    construction_year smallint,
    dist_metro_km     double precision,
    lon               double precision NOT NULL,
    lat               double precision NOT NULL,
    h3_fine           text             NOT NULL,
    h3_coarse         text             NOT NULL,
    PRIMARY KEY (city, asset_id, period)
);

CREATE TABLE IF NOT EXISTS staging.listings_rejected (
    city     text NOT NULL,
    asset_id text NOT NULL,
    period   date NOT NULL,
    reason   text NOT NULL,
    lon      double precision,
    lat      double precision
);
CREATE INDEX IF NOT EXISTS listings_rejected_city_reason_idx ON staging.listings_rejected (city, reason);

CREATE TABLE IF NOT EXISTS staging.zones (
    city      text NOT NULL,
    zone_id   text NOT NULL,
    zone_name text NOT NULL,
    geom      geometry(MultiPolygon, 4326) NOT NULL,
    PRIMARY KEY (city, zone_id)
);

-- ---------------------------------------------------------------- core
CREATE TABLE IF NOT EXISTS core.zones (
    city      text NOT NULL,
    zone_id   text NOT NULL,
    zone_name text NOT NULL,
    geom      geometry(MultiPolygon, 25830) NOT NULL,
    PRIMARY KEY (city, zone_id)
);
CREATE INDEX IF NOT EXISTS zones_geom_gix ON core.zones USING gist (geom);

CREATE TABLE IF NOT EXISTS core.listings (
    city              text             NOT NULL,
    asset_id          text             NOT NULL,
    period            date             NOT NULL,
    zone_id           text             NOT NULL,
    zone_distance_m   double precision NOT NULL DEFAULT 0,  -- > 0 when snapped into a polygon gap
    price             double precision NOT NULL CHECK (price > 0),
    area_m2           double precision NOT NULL CHECK (area_m2 > 0),
    price_m2          double precision NOT NULL,
    price_m2_adj      double precision,           -- brought to the latest quarter (time index)
    rooms             smallint,
    baths             smallint,
    has_lift          boolean,
    has_terrace       boolean,
    has_parking       boolean,
    is_studio         boolean,
    construction_year smallint,
    dist_metro_km     double precision,
    h3_fine           text             NOT NULL,
    h3_coarse         text             NOT NULL,
    geom              geometry(Point, 25830) NOT NULL,
    PRIMARY KEY (city, asset_id, period)
);
CREATE INDEX IF NOT EXISTS listings_geom_gix ON core.listings USING gist (geom);
CREATE INDEX IF NOT EXISTS listings_zone_idx ON core.listings (city, zone_id);
CREATE INDEX IF NOT EXISTS listings_h3_fine_idx ON core.listings (h3_fine);
CREATE INDEX IF NOT EXISTS listings_h3_coarse_idx ON core.listings (h3_coarse);

CREATE TABLE IF NOT EXISTS core.time_index (
    city            text             NOT NULL,
    period          date             NOT NULL,
    n               integer          NOT NULL,
    median_price_m2 double precision NOT NULL,
    factor          double precision,  -- multiply a price of `period` to express it in latest-quarter terms
    PRIMARY KEY (city, period)
);

CREATE TABLE IF NOT EXISTS core.h3_cells (
    h3   text     PRIMARY KEY,
    res  smallint NOT NULL,
    geom geometry(Polygon, 4326) NOT NULL
);

-- ---------------------------------------------------------------- marts (published as vector tiles)
CREATE TABLE IF NOT EXISTS marts.hex_fine (
    city                 text     NOT NULL,
    h3                   text     NOT NULL,
    n                    integer  NOT NULL,
    median_m2            integer  NOT NULL,  -- smoothed towards parent cell when n is small
    raw_median_m2        integer  NOT NULL,
    p25_m2               integer  NOT NULL,
    p75_m2               integer  NOT NULL,
    confidence           text     NOT NULL,  -- low | medium | high (by sample size)
    outlier_vs_neighbors boolean  NOT NULL DEFAULT false,
    geom                 geometry(Polygon, 4326) NOT NULL,
    PRIMARY KEY (city, h3)
);
CREATE INDEX IF NOT EXISTS hex_fine_geom_gix ON marts.hex_fine USING gist (geom);

CREATE TABLE IF NOT EXISTS marts.hex_coarse (
    city      text    NOT NULL,
    h3        text    NOT NULL,
    n         integer NOT NULL,
    median_m2 integer NOT NULL,
    p25_m2    integer NOT NULL,
    p75_m2    integer NOT NULL,
    geom      geometry(Polygon, 4326) NOT NULL,
    PRIMARY KEY (city, h3)
);
CREATE INDEX IF NOT EXISTS hex_coarse_geom_gix ON marts.hex_coarse USING gist (geom);

CREATE TABLE IF NOT EXISTS marts.zone_prices (
    city            text    NOT NULL,
    zone_id         text    NOT NULL,
    zone_name       text    NOT NULL,
    n               integer NOT NULL,
    median_m2       integer,  -- NULL when there are too few listings to publish
    p25_m2          integer,
    p75_m2          integer,
    median_m2_1room integer,
    median_m2_2room integer,
    median_m2_3room integer,
    median_m2_4plus integer,
    geom            geometry(MultiPolygon, 4326) NOT NULL,
    PRIMARY KEY (city, zone_id)
);
CREATE INDEX IF NOT EXISTS zone_prices_geom_gix ON marts.zone_prices USING gist (geom);

-- ---------------------------------------------------------------- ops
CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    city        text        NOT NULL,
    started_at  timestamptz NOT NULL,
    finished_at timestamptz NOT NULL DEFAULT now(),
    stats       jsonb       NOT NULL
);
