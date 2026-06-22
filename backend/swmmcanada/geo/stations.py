"""Station discovery (spec 01 §3.4): select climate + hydrometric stations whose point
locations fall inside the AOI polygon. The data source is injected (a Protocol), so the
selection logic is offline-testable and the same clients are reusable by `acquire.*`.
"""
from dataclasses import dataclass
from typing import Protocol, Tuple

import geopandas as gpd

from swmmcanada.geo.aoi import AOI

Bbox = Tuple[float, float, float, float]


class StationSource(Protocol):
    """A source of station metadata. In production a thin wrapper over the ECCC GeoMet
    OGC API (climate-stations / hydrometric-stations); in tests, recorded fixtures."""

    def stations_in_bbox(self, bbox: Bbox) -> gpd.GeoDataFrame:
        ...


@dataclass(frozen=True)
class StationSelection:
    climate: gpd.GeoDataFrame   # climate stations within the AOI (ECCC; rainfall/temp forcing)
    hydro: gpd.GeoDataFrame     # hydrometric stations within the AOI (WSC/HYDAT; calibration)


def select_stations(aoi: AOI, *, climate: StationSource, hydro: StationSource) -> StationSelection:
    """bbox pre-filter at the source, then exact point-in-polygon against AOI.geometry.
    Empty results are non-fatal (downstream decides)."""
    return StationSelection(
        climate=_within_aoi(climate.stations_in_bbox(aoi.bbox), aoi),
        hydro=_within_aoi(hydro.stations_in_bbox(aoi.bbox), aoi),
    )


def _within_aoi(gdf: gpd.GeoDataFrame, aoi: AOI) -> gpd.GeoDataFrame:
    """Keep only stations whose point falls inside the AOI polygon (the bbox is just the
    cheap source-side pre-filter; the polygon is the truth)."""
    if gdf is None or len(gdf) == 0:
        return gdf
    inside = gdf[gdf.geometry.within(aoi.geometry)]
    return inside.reset_index(drop=True)
