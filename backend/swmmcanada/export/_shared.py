"""Small helpers shared by the non-SWMM exporters (MIKE+, ICM) — geometry scaffolding and
the plain rainfall CSV. Kept here so the second/third exporter doesn't copy the first."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Optional, Tuple

import geopandas as gpd
from shapely.geometry import Polygon


def node_lookup(network) -> Dict[str, Tuple[float, float]]:
    """name → (lon, lat) over junctions + outfalls (link endpoints + catchment placeholders)."""
    xy: Dict[str, Tuple[float, float]] = {}
    for n in list(network.junctions) + list(network.outfalls):
        xy[n.name] = (float(n.x), float(n.y))
    return xy


def to_crs(gdf: gpd.GeoDataFrame, crs: Optional[str]) -> gpd.GeoDataFrame:
    """Reproject 4326→``crs`` when set so ``.to_file`` writes a matching ``.prj``."""
    return gdf.to_crs(crs) if crs else gdf


def placeholder_square(area_m2: float, center_lonlat: Optional[Tuple[float, float]]) -> Polygon:
    """A square of side √area centred on the outlet node, built in 4326 (reprojected with
    the layer). Falls back to origin if the outlet node coordinate is unknown.

    The side is derived in metres but drawn as a degree offset — placeholder geometry only,
    never used for computation (authoritative areas come from the attributes).
    """
    lon, lat = center_lonlat if center_lonlat else (0.0, 0.0)
    half_deg = (area_m2 ** 0.5) / 111_320.0 / 2.0  # ~metres per degree at the equator
    return Polygon([
        (lon - half_deg, lat - half_deg),
        (lon + half_deg, lat - half_deg),
        (lon + half_deg, lat + half_deg),
        (lon - half_deg, lat + half_deg),
    ])


def write_rain_csv(path: Path, rain) -> Path:
    """Two-column CSV (``datetime,rainfall_mm``, ISO datetimes) — the portable fallback
    carrier every target can ingest one way or another."""
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["datetime", "rainfall_mm"])
        for ts, mm in zip(rain.timestamps, rain.precip_mm):
            w.writerow([ts.isoformat(), float(mm)])
    return path
