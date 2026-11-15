"""Runtime configuration, read from environment variables (12-factor).

Every setting can be overridden with ``GEOSTATE_<NAME>``, e.g.
``GEOSTATE_PG_DSN=postgresql://... geostate run``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEOSTATE_", env_file=".env", extra="ignore")

    pg_dsn: str = "postgresql://geostate:geostate@localhost:5433/geostate"

    # ClickHouse: full listing history for large-scale analytics (time series)
    ch_host: str = "localhost"
    ch_port: int = 8123
    ch_user: str = "geostate"
    ch_password: str = "geostate"
    ch_database: str = "geostate"
    data_dir: Path = Path("data/raw")
    source_base_url: str = "https://github.com/paezha/idealista18/raw/master/data"

    # Two-level honeycomb (RealAdvisor-style): fine cells for local detail,
    # coarse cells for regional synthesis and for smoothing sparse fine cells.
    h3_fine_res: int = Field(default=9, ge=0, le=15)
    h3_coarse_res: int = Field(default=7, ge=0, le=15)

    # Publishing rules
    min_listings_per_cell: int = Field(default=3, ge=1)
    shrinkage_k: float = Field(default=5.0, ge=0)  # pseudo-count pulling sparse cells to parent

    # Geo QA: listings within this distance of a zone are snapped to it (polygon sliver gaps)
    zone_snap_m: float = Field(default=50.0, ge=0)

    # Quality gates
    max_outside_city_ratio: float = Field(default=0.05, ge=0, le=1)
    min_zone_coverage: float = Field(default=0.8, ge=0, le=1)
    outlier_ratio_low: float = 0.6
    outlier_ratio_high: float = 1.6


@lru_cache
def get_settings() -> Settings:
    return Settings()
