import h3
import numpy as np
import pandas as pd
import pytest
from shapely import wkt

from geostate.spatial import assign_h3, cell_polygon_ewkt, neighbor_outliers, sf_to_shapely

PUERTA_DEL_SOL = (40.4169, -3.7035)


def test_coarse_cell_is_parent_of_fine_cell():
    df = pd.DataFrame({"lat": [PUERTA_DEL_SOL[0]], "lon": [PUERTA_DEL_SOL[1]]})
    out = assign_h3(df, fine_res=9, coarse_res=7)
    fine, coarse = out.loc[0, "h3_fine"], out.loc[0, "h3_coarse"]
    assert h3.get_resolution(fine) == 9
    assert h3.cell_to_parent(fine, 7) == coarse


def test_assign_h3_rejects_inverted_resolutions():
    with pytest.raises(ValueError):
        assign_h3(pd.DataFrame({"lat": [40.0], "lon": [-3.0]}), fine_res=7, coarse_res=9)


def test_cell_polygon_is_closed_lon_lat_and_contains_its_center():
    cell = h3.latlng_to_cell(*PUERTA_DEL_SOL, 9)
    ewkt = cell_polygon_ewkt(cell)
    assert ewkt.startswith("SRID=4326;POLYGON")
    poly = wkt.loads(ewkt.split(";", 1)[1])
    lat, lng = h3.cell_to_latlng(cell)
    assert poly.is_valid
    assert poly.contains(wkt.loads(f"POINT({lng} {lat})"))  # x = longitude


def square(x0, y0, size=1.0):
    return np.array([[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]])


def test_sf_polygon_and_multipolygon_are_converted():
    poly = sf_to_shapely([square(0, 0)])
    multi = sf_to_shapely([[square(0, 0)], [square(5, 5)]])
    assert poly.geom_type == "MultiPolygon" and poly.area == pytest.approx(1)
    assert len(multi.geoms) == 2


def test_neighbor_outlier_is_flagged_only_when_far_from_neighbours():
    center = h3.latlng_to_cell(*PUERTA_DEL_SOL, 9)
    ring = list(h3.grid_ring(center, 1))
    medians = {c: 5_000.0 for c in ring}
    medians[center] = 12_000.0
    flagged = neighbor_outliers(medians, low=0.6, high=1.6)
    assert center in flagged and flagged[center] == pytest.approx(2.4)
    assert not set(ring) & set(flagged)  # ring cells are close to the median of their neighbours


def test_cells_without_enough_neighbours_are_not_judged():
    center = h3.latlng_to_cell(*PUERTA_DEL_SOL, 9)
    one_neighbour = next(iter(h3.grid_ring(center, 1)))
    assert neighbor_outliers({center: 20_000.0, one_neighbour: 1_000.0}, 0.6, 1.6) == {}
