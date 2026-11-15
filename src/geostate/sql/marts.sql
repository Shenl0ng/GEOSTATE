-- Build published marts for one city. Run inside one transaction together with the
-- quality gates: if a gate fails, everything rolls back and the map keeps the last good data.
-- Params: %(city)s, %(min_n)s, %(k)s

-- Coarse honeycomb (regional synthesis).
DELETE FROM marts.hex_coarse WHERE city = %(city)s;

INSERT INTO marts.hex_coarse (city, h3, n, median_m2, p25_m2, p75_m2, geom)
SELECT l.city, l.h3_coarse, count(*),
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY l.price_m2_adj)),
       round(percentile_cont(0.25) WITHIN GROUP (ORDER BY l.price_m2_adj)),
       round(percentile_cont(0.75) WITHIN GROUP (ORDER BY l.price_m2_adj)),
       c.geom
FROM core.listings l
JOIN core.h3_cells c ON c.h3 = l.h3_coarse
WHERE l.city = %(city)s
GROUP BY l.city, l.h3_coarse, c.geom
HAVING count(*) >= %(min_n)s;

-- Fine honeycomb (local precision). Sparse cells are shrunk towards their parent:
--   smoothed = (n * cell_median + k * parent_median) / (n + k)
-- With many listings the cell speaks for itself; with few it borrows strength from the area.
DELETE FROM marts.hex_fine WHERE city = %(city)s;

WITH fine AS (
    SELECT city, h3_fine, h3_coarse, count(*) AS n,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY price_m2_adj) AS med,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY price_m2_adj) AS p25,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY price_m2_adj) AS p75
    FROM core.listings
    WHERE city = %(city)s
    GROUP BY city, h3_fine, h3_coarse
),
parent AS (
    SELECT h3_coarse, percentile_cont(0.50) WITHIN GROUP (ORDER BY price_m2_adj) AS med
    FROM core.listings
    WHERE city = %(city)s
    GROUP BY h3_coarse
)
INSERT INTO marts.hex_fine (city, h3, n, median_m2, raw_median_m2, p25_m2, p75_m2, confidence, geom)
SELECT f.city, f.h3_fine, f.n,
       round((f.n * f.med + %(k)s * p.med) / (f.n + %(k)s)),
       round(f.med), round(f.p25), round(f.p75),
       CASE WHEN f.n >= 10 THEN 'high' WHEN f.n >= 5 THEN 'medium' ELSE 'low' END,
       c.geom
FROM fine f
JOIN parent p USING (h3_coarse)
JOIN core.h3_cells c ON c.h3 = f.h3_fine
WHERE f.n >= %(min_n)s;

-- Neighbourhood prices, overall and by number of rooms (spatial join already done in core).
DELETE FROM marts.zone_prices WHERE city = %(city)s;

INSERT INTO marts.zone_prices (
    city, zone_id, zone_name, n, median_m2, p25_m2, p75_m2,
    median_m2_1room, median_m2_2room, median_m2_3room, median_m2_4plus, geom
)
SELECT z.city, z.zone_id, z.zone_name, count(l.asset_id),
       CASE WHEN count(l.asset_id) >= %(min_n)s THEN
           round(percentile_cont(0.50) WITHIN GROUP (ORDER BY l.price_m2_adj)) END,
       CASE WHEN count(l.asset_id) >= %(min_n)s THEN
           round(percentile_cont(0.25) WITHIN GROUP (ORDER BY l.price_m2_adj)) END,
       CASE WHEN count(l.asset_id) >= %(min_n)s THEN
           round(percentile_cont(0.75) WITHIN GROUP (ORDER BY l.price_m2_adj)) END,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY l.price_m2_adj) FILTER (WHERE l.rooms <= 1)),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY l.price_m2_adj) FILTER (WHERE l.rooms = 2)),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY l.price_m2_adj) FILTER (WHERE l.rooms = 3)),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY l.price_m2_adj) FILTER (WHERE l.rooms >= 4)),
       ST_Multi(ST_Transform(z.geom, 4326))
FROM core.zones z
LEFT JOIN core.listings l ON l.city = z.city AND l.zone_id = z.zone_id
WHERE z.city = %(city)s
GROUP BY z.city, z.zone_id, z.zone_name, z.geom;
