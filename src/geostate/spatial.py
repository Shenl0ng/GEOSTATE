"""Spatial helpers: H3 indexing, geometry conversion, neighbour checks."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from typing import Any

import h3
import pandas as pd
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


def assign_h3(df: pd.DataFrame, fine_res: int, coarse_res: int) -> pd.DataFrame:
    """Add ``h3_fine`` and ``h3_coarse`` columns from lat/lon (EPSG:4326).

    The coarse cell is the *parent* of the fine cell (not re-indexed from the point),
    so every fine cell belongs to exactly one coarse cell and the hierarchy is consistent.
    """
    if coarse_res >= fine_res:
        raise ValueError("coarse resolution must be lower than fine resolution")
    fine = [
        h3.latlng_to_cell(lat, lon, fine_res) for lat, lon in zip(df["lat"], df["lon"], strict=True)
    ]
    return df.assign(h3_fine=fine, h3_coarse=[h3.cell_to_parent(c, coarse_res) for c in fine])


def cell_polygon_ewkt(cell: str) -> str:
    """H3 cell boundary as EWKT polygon (lon lat order, closed ring)."""
    ring = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]
    ring.append(ring[0])
    coords = ", ".join(f"{x:.7f} {y:.7f}" for x, y in ring)
    return f"SRID=4326;POLYGON(({coords}))"


def _depth(obj: Any) -> int:
    depth = 0
    while isinstance(obj, list):
        depth += 1
        obj = obj[0]
    return depth


def sf_to_shapely(geom: Any) -> BaseGeometry:
    """Convert an R ``sf`` polygon / multipolygon (nested lists of Nx2 arrays) to shapely.

    POLYGON      = [outer_ring, hole, ...]
    MULTIPOLYGON = [[outer_ring, hole, ...], ...]
    """
    depth = _depth(geom)
    if depth == 1:
        polygons = [Polygon(geom[0], geom[1:])]
    elif depth == 2:
        polygons = [Polygon(rings[0], rings[1:]) for rings in geom]
    else:
        raise ValueError(f"unsupported sf geometry nesting depth {depth}")
    result = MultiPolygon(polygons)
    return result if result.is_valid else make_valid(result)


def neighbor_outliers(
    medians: Mapping[str, float],
    low: float,
    high: float,
    min_neighbors: int = 3,
) -> dict[str, float]:
    """Cells whose price differs too much from their immediate H3 neighbours.

    Returns ``{cell: ratio}`` where ratio = cell median / median of neighbour medians,
    only for cells with at least ``min_neighbors`` published neighbours and a ratio
    outside ``[low, high]``.
    """
    flagged: dict[str, float] = {}
    for cell, value in medians.items():
        neighbours: Iterable[str] = h3.grid_ring(cell, 1)
        values = [medians[n] for n in neighbours if n in medians]
        if len(values) < min_neighbors:
            continue
        ratio = value / statistics.median(values)
        if not low <= ratio <= high:
            flagged[cell] = ratio
    return flagged
