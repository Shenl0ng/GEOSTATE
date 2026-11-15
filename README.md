# GeoEstate

End-to-end geospatial pipeline that turns raw real-estate **listings** into a **price honeycomb**
and **neighbourhood prices**, served as **vector tiles** on an interactive map.

It is a small-scale replication of the approach described in RealAdvisor's public
[price methodology](https://realadvisor.es/es/blog/metodologia-precio-vivienda): listing
deduplication, geolocation quality control, temporal adjustment, a two-level honeycomb
with smoothing in sparse areas, and automated checks against neighbouring cells.

```
idealista18 listings ─► stage (clean, dedupe, H3) ─► PostGIS core (spatial join, geo QA,
  (Madrid, 2018)         Python                      time index)  EPSG:25830
                                                          │
                     map (MapLibre / Mapbox GL) ◄─ Martin ◄─ marts: hex_fine (H3 r9),
                                                   (MVT)        hex_coarse (H3 r7), zone_prices
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
make up        # PostGIS + Martin + frontend
make pipeline  # download data, run the pipeline for Madrid, print run stats
open http://localhost:8080
```

`make test` runs the unit tests, `make lint` runs ruff + mypy.

Optional: put a Mapbox token in `frontend/config.js` to render with Mapbox GL JS;
without it the map uses MapLibre GL JS (same API) with an OpenStreetMap basemap.

## Design

- **Layers**: `staging` (as loaded) → `core` (clean, one row per listing and quarter) → `marts` (served).
- **CRS**: compute in EPSG:25830 (ETRS89 / UTM 30N, metric, official for mainland Spain); marts
  in 4326; tiles in 3857 (done by Martin). Web Mercator is never used for measurements.
- **Idempotent steps**: every step deletes and rewrites its city, so retries and re-runs are safe.
- **Atomic publish**: marts and their quality gates run in one transaction; if a gate fails,
  the transaction rolls back and the map keeps serving the last good version.
- **Orchestrator-ready**: `stage`, `build_core` and `build_marts` exchange data through the
  database, not memory, so each one can be its own Airflow task.

## Layout

```
src/geostate/   extract · transform · spatial · load · quality · pipeline · cli
src/geostate/sql/   schema.sql · core.sql · marts.sql
tests/unit/     pure-function tests (no database needed)
docker/         Martin config
frontend/       single-page map
```

## Roadmap

- [x] Phase 1: listings → PostGIS → H3 honeycomb → vector tiles → map
- [ ] Phase 2: Airflow DAG, ClickHouse for price history, CI
- [ ] Phase 3: Kubernetes (kind + Helm), KubernetesPodOperator
- [ ] Street-level prices (OpenStreetMap streets), Barcelona and Valencia
