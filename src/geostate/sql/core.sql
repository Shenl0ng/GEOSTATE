-- Build core layer for one city. Run inside one transaction (all or nothing).
-- Params: %(city)s

-- 1. Zones: fix invalid polygons, reproject to metric CRS.
DELETE FROM core.zones WHERE city = %(city)s;

INSERT INTO core.zones (city, zone_id, zone_name, geom)
SELECT city, zone_id, zone_name,
       ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(geom, 25830)), 3))
FROM staging.zones
WHERE city = %(city)s;

-- 2. Geo QA + spatial join. Each listing gets exactly one zone: the one containing it, or the
--    nearest one within %(snap_m)s metres. The tolerance exists because neighbouring polygons do
--    not tile perfectly (the union of Madrid's 135 zones has 217 interior sliver gaps), so valid
--    listings fall in gaps. LIMIT 1 also avoids double counting points on shared borders.
--    Listings farther than the tolerance from every zone are geolocation errors -> rejected.
DELETE FROM core.listings WHERE city = %(city)s;

DELETE FROM staging.listings_rejected WHERE city = %(city)s AND reason = 'outside_city';

WITH pts AS (
    SELECT s.*, ST_Transform(ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326), 25830) AS geom
    FROM staging.listings s
    WHERE s.city = %(city)s
),
joined AS (
    SELECT p.*, z.zone_id, z.zone_distance_m
    FROM pts p
    LEFT JOIN LATERAL (
        SELECT zone_id, ST_Distance(z.geom, p.geom) AS zone_distance_m
        FROM core.zones z
        WHERE z.city = p.city AND ST_DWithin(z.geom, p.geom, %(snap_m)s)  -- uses GiST index
        ORDER BY ST_Distance(z.geom, p.geom), zone_id                        -- inside = 0 m
        LIMIT 1
    ) z ON true
),
rejected AS (
    INSERT INTO staging.listings_rejected (city, asset_id, period, reason, lon, lat)
    SELECT city, asset_id, period, 'outside_city', lon, lat
    FROM joined
    WHERE zone_id IS NULL
    RETURNING 1
)
INSERT INTO core.listings (
    city, asset_id, period, zone_id, zone_distance_m, price, area_m2, price_m2, rooms, baths,
    has_lift, has_terrace, has_parking, is_studio, construction_year, dist_metro_km,
    h3_fine, h3_coarse, geom
)
SELECT city, asset_id, period, zone_id, zone_distance_m, price, area_m2, price_m2, rooms, baths,
       has_lift, has_terrace, has_parking, is_studio, construction_year, dist_metro_km,
       h3_fine, h3_coarse, geom
FROM joined
WHERE zone_id IS NOT NULL;

-- 3. Time adjustment. Quarterly index = city median €/m² per quarter. Prices of older
--    quarters are scaled to the latest quarter so all listings are comparable.
--    Limitation (documented): one city-wide index; areas that move faster are under-adjusted.
DELETE FROM core.time_index WHERE city = %(city)s;

INSERT INTO core.time_index (city, period, n, median_price_m2)
SELECT city, period, count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY price_m2)
FROM core.listings
WHERE city = %(city)s
GROUP BY city, period;

UPDATE core.time_index t
SET factor = latest.median_price_m2 / t.median_price_m2
FROM (
    SELECT median_price_m2 FROM core.time_index
    WHERE city = %(city)s ORDER BY period DESC LIMIT 1
) latest
WHERE t.city = %(city)s;

UPDATE core.listings l
SET price_m2_adj = l.price_m2 * t.factor
FROM core.time_index t
WHERE t.city = l.city AND t.period = l.period AND l.city = %(city)s;

ANALYZE core.listings;
