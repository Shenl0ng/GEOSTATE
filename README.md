# GeoEstate

End-to-end geospatial pipeline that turns raw real-estate **listings** into a **price honeycomb**
and **neighbourhood prices**, served as **vector tiles** on an interactive map.

It is a small-scale replication of the approach described in RealAdvisor's public
[price methodology](https://realadvisor.es/es/blog/metodologia-precio-vivienda): listing
deduplication, geolocation quality control, temporal adjustment, a two-level honeycomb
with smoothing in sparse areas, and automated checks against neighbouring cells.

```
                    Airflow DAG  listings_pipeline  (one task per step, retries, quality gates)
                    ┌──────────────────┬──────────────────────┬──────────────────┬───────────────┐
idealista18 ──► stage                ► core                 ► marts            ► clickhouse
(Madrid 2018)   clean, dedupe, H3      spatial join, geo QA,  H3 r9/r7, zones,   history +
                → PostGIS staging      time index (25830)     checks (atomic)    aggregates
                                                                    │                 │
                               map (Mapbox GL) ◄── Martin (MVT) ◄───┘     trends per zone
```

![Fine honeycomb (H3 r9) over central Madrid, with tooltip and a cell flagged for review](docs/img/honeycomb-fine.png)

## Results on Madrid (one run, ~20 s on a laptop)

| Step | Rows |
|---|---|
| Raw listings | 94,815 |
| Duplicates removed (same asset, same quarter) | −19,011 |
| Snapped into polygon gaps (≤ 50 m from a zone) | 50 kept |
| Rejected: outside the city (21 in neighbouring municipalities, 1 in Andalusia) | −22 |
| **Clean listings in `core`** | **75,782** |
| Published cells: H3 r9 / H3 r7 / neighbourhoods | 1,556 / 69 / 135 (100% coverage) |
| Cells flagged as atypical vs. neighbours (for review) | 10 |

Quarterly time index (city median €/m², latest quarter = 1.0): Q1 ×1.087, Q2 ×1.047,
Q3 ×1.022. Asking prices rose ~8.7% during 2018. Most expensive neighbourhood: Recoletos
(7,718 €/m²); cheapest: San Cristóbal (1,203 €/m²).

Airflow: both runs green; the manual run waited in the queue (grey) because `max_active_runs=1`.

![Airflow DAG listings_pipeline: 4 tasks, 2 successful runs](docs/img/airflow.png)

| Coarse honeycomb (H3 r7) | Neighbourhoods |
|---|---|
| ![](docs/img/honeycomb-coarse.png) | ![](docs/img/zones.png) |

## Data

[idealista18](https://github.com/paezha/idealista18) (ODbL): ~95k real sale listings from
Idealista for Madrid in 2018 (4 quarters), with price, size, features and coordinates,
plus 135 neighbourhood polygons. Real issues handled by the pipeline:

| Issue in the source | Handling |
|---|---|
| Same asset listed several times in one quarter, at different prices | keep lowest asking price per (asset, quarter) |
| Neighbourhood polygons don't tile perfectly (217 sliver gaps in their union) | listings ≤ 50 m from a zone are snapped to the nearest one (KNN), distance stored in `zone_distance_m` |
| Listings geolocated outside the city | rejected by spatial join, stored with reason, blocking gate if > 5% |
| Prices from four different quarters | city-wide quarterly index, prices expressed in latest-quarter terms |
| Sparse cells | shrinkage towards the parent H3 cell, `confidence` attribute, `n >= 3` to publish |
| Atypical cells | compared with their H3 neighbours on the **raw** median (smoothing would hide them), flagged for review |

Asking prices are not transaction prices: the outputs are asking-price indicators.

## Run it

Requirements: Docker, [uv](https://docs.astral.sh/uv/).

```bash
make install   # Python deps (uv)
make up        # PostGIS, ClickHouse, Airflow, Martin, frontend
make pipeline  # run all steps from the terminal (or trigger the DAG in Airflow)
open http://localhost:8080   # map
open http://localhost:8081   # Airflow UI (local, no login)
make trends    # price change per neighbourhood, from ClickHouse
```

`make test` runs the unit tests, `make lint` runs ruff + mypy. CI (GitHub Actions) runs those
plus the full pipeline twice against real PostGIS and ClickHouse services, to prove idempotency,
and checks that the Airflow DAG imports.

Optional: put a Mapbox token in `frontend/config.js` to render with Mapbox GL JS;
without it the map uses MapLibre GL JS (same API) with an OpenStreetMap basemap.

## Design

- **Layers**: `staging` (as loaded) → `core` (clean, one row per listing and quarter) → `marts` (served).
- **CRS**: compute in EPSG:25830 (ETRS89 / UTM 30N, metric, official for mainland Spain); marts
  in 4326; tiles in 3857 (done by Martin). Web Mercator is never used for measurements.
- **Idempotent steps**: every step deletes and rewrites its city, so retries and re-runs are safe.
- **Atomic publish**: marts and their quality gates run in one transaction; if a gate fails,
  the transaction rolls back and the map keeps serving the last good version.
- **Orchestration**: each step is a CLI command (`geostate stage|core|marts|clickhouse`) that
  exchanges data through the database, not memory. The Airflow DAG only orchestrates: one task
  per step, the package lives in its own virtualenv inside the Airflow image (no dependency
  clashes), infrastructure errors are retried with backoff, and quality-gate failures
  (exit code 2) fail fast without retry. `max_active_runs=1` because runs rewrite the same tables.
- **ClickHouse**: flat, enriched copy of clean listings (`MergeTree`, partitioned by
  city and month) plus an `AggregatingMergeTree` fed by a materialized view (quarterly median
  per zone). Re-runs drop the city's partitions in both tables first: a materialized view
  only reacts to inserts, so stale aggregates would otherwise be double counted.

## Layout

```
src/geostate/       extract · transform · spatial · load · quality · clickhouse · pipeline · cli
src/geostate/sql/   schema.sql · core.sql · marts.sql · clickhouse_*.sql
dags/               Airflow DAG (orchestration only)
tests/unit/     pure-function tests (no database needed)
docker/         Airflow image, Martin config, Postgres init
frontend/       single-page map
```

## Roadmap

- [x] Phase 1: listings → PostGIS → H3 honeycomb → vector tiles → map
- [x] Phase 2: Airflow DAG, ClickHouse for price history, CI
- [ ] Phase 3: Kubernetes (kind + Helm), KubernetesPodOperator
- [ ] Street-level prices (OpenStreetMap streets), Barcelona and Valencia
