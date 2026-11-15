-- Price change per neighbourhood between the first and last quarter available.
-- Reads the pre-aggregated table (aggregate states merged at query time).
WITH q AS (
    SELECT zone_name, period,
           countMerge(n) AS n,
           quantileMerge(0.5)(median_m2) AS med
    FROM zone_quarter_stats
    WHERE city = {city:String}
    GROUP BY zone_name, period
)
SELECT zone_name,
       round(argMin(med, period)) AS first_quarter_m2,
       round(argMax(med, period)) AS last_quarter_m2,
       round(last_quarter_m2 / first_quarter_m2 - 1, 3) AS change,
       sum(n) AS listings
FROM q
GROUP BY zone_name
HAVING min(n) >= {min_n:UInt32} AND count() >= 2
ORDER BY change DESC
