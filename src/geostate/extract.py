"""Extract: download and read the idealista18 open dataset (ODbL).

Source: https://github.com/paezha/idealista18 (R ``.rda`` files).
"""

from __future__ import annotations

import logging
import shutil
import urllib.request
import warnings
from pathlib import Path
from typing import Literal

import pandas as pd
import rdata

from geostate.spatial import sf_to_shapely

log = logging.getLogger(__name__)

SourceKind = Literal["Sale", "Polygons"]


def download_source(city: str, kind: SourceKind, base_url: str, data_dir: Path) -> Path:
    """Download ``<City>_<kind>.rda`` once. Idempotent and atomic (write .part, then rename)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / f"{city}_{kind}.rda"
    if dest.exists() and dest.stat().st_size > 0:
        log.info("source already downloaded: %s", dest)
        return dest

    url = f"{base_url}/{dest.name}"
    tmp = dest.with_suffix(".part")
    log.info("downloading %s", url)
    with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    tmp.replace(dest)
    return dest


def read_rda(path: Path) -> pd.DataFrame:
    """Read the single data frame stored in an ``.rda`` file."""
    with warnings.catch_warnings():
        # rdata warns about R classes it doesn't know (sf, crs, bbox); the data itself is fine.
        warnings.simplefilter("ignore", UserWarning)
        parsed = rdata.parser.parse_file(path)
        converted = rdata.conversion.convert(parsed, default_encoding="utf8")
    if len(converted) != 1:
        raise ValueError(f"expected one object in {path}, found {list(converted)}")
    df = next(iter(converted.values()))
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"{path} does not contain a data frame")
    return df


def read_zones(path: Path) -> pd.DataFrame:
    """Neighbourhood polygons -> DataFrame(zone_id, zone_name, ewkt) in EPSG:4326."""
    raw = read_rda(path)
    zones = pd.DataFrame(
        {
            "zone_id": raw["LOCATIONID"].astype(str),
            "zone_name": raw["LOCATIONNAME"].astype(str),
            "ewkt": ["SRID=4326;" + sf_to_shapely(g).wkt for g in raw["geometry"]],
        }
    )
    if zones["zone_id"].duplicated().any():
        raise ValueError("duplicated zone ids in polygons source")
    return zones
