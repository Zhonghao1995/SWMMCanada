"""acquire.dem (spec 04): an AOI bbox → a DEM GeoTIFF clipped to that bbox, from NRCan
CanElevation (MRDEM-30 national / HRDEM where available).

The STAC/COG *discovery* is behind an injected `DemSource` (so selection is testable
offline); the windowed *clip* is a pure rasterio op tested against a local fixture.
v1 covers the MVP path: a source already in EPSG:3979 written out as EPSG:3979 (no
reprojection — MRDEM's native CRS). Cross-CRS reprojection is a later increment.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol, Tuple

import rasterio
from rasterio.windows import Window, bounds as window_bounds, from_bounds, intersection
from rasterio.warp import transform_bounds

from swmmcanada.acquire.errors import DegenerateBboxError, DemError, NoDemCoverageError

Bbox = Tuple[float, float, float, float]


@dataclass(frozen=True)
class DemAsset:
    """A chosen COG to read, returned by a DemSource."""
    dtm_href: str
    dsm_href: Optional[str]
    source: str            # "hrdem-lidar" | "mrdem-30"
    resolution_m: float
    crs: str               # the COG's native CRS (e.g. "EPSG:3979")
    item_ids: List[str]
    coverage: str          # "full" | "partial" | "fallback"


@dataclass(frozen=True)
class DemResult:
    path: Path
    dsm_path: Optional[Path]
    source: str
    resolution_m: float
    crs: str               # CRS of the written raster
    bbox: Bbox             # actual clipped extent, in `crs`
    coverage: str
    item_ids: List[str]


class DemSource(Protocol):
    """Selects the DEM COG covering a WGS84 bbox. Production impl wraps the NRCan STAC API
    + S3 COGs; tests inject a fake returning a local fixture."""

    def select(self, bbox_wgs84: Bbox, prefer: str) -> Optional[DemAsset]:
        ...


def acquire_dem(
    aoi_bbox_wgs84: Bbox,
    workspace: "Path | str",
    *,
    source: DemSource,
    prefer: str = "auto",
    out_crs: str = "EPSG:3979",
    target_res_m: Optional[float] = None,
    want_dsm: bool = False,
) -> DemResult:
    _validate_bbox(aoi_bbox_wgs84)
    asset = source.select(aoi_bbox_wgs84, prefer)
    if asset is None:
        raise NoDemCoverageError(f"No DEM source covers bbox {aoi_bbox_wgs84}.")

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)

    dtm_path = ws / "dem_dtm.tif"
    clipped_bbox = _clip_cog_to_bbox(asset.dtm_href, aoi_bbox_wgs84, dtm_path, out_crs)

    dsm_path: Optional[Path] = None
    if want_dsm and asset.dsm_href:
        dsm_path = ws / "dem_dsm.tif"
        _clip_cog_to_bbox(asset.dsm_href, aoi_bbox_wgs84, dsm_path, out_crs)

    return DemResult(
        path=dtm_path,
        dsm_path=dsm_path,
        source=asset.source,
        resolution_m=asset.resolution_m,
        crs=out_crs,
        bbox=clipped_bbox,
        coverage=asset.coverage,
        item_ids=list(asset.item_ids),
    )


# --- internals ---------------------------------------------------------------


def _validate_bbox(bbox: Bbox) -> None:
    minx, miny, maxx, maxy = bbox
    if not (minx < maxx and miny < maxy):
        raise DegenerateBboxError(f"Degenerate AOI bbox: {bbox}")


def _clip_cog_to_bbox(href: str, bbox_4326: Bbox, out_path: Path, out_crs: str) -> Bbox:
    """Windowed read of a COG clipped to the bbox; write a GeoTIFF. v1 requires the COG
    CRS to equal out_crs (the MRDEM native-EPSG:3979 path)."""
    with rasterio.open(href) as src:
        if src.crs is None or (src.crs.to_epsg() != rasterio.crs.CRS.from_string(out_crs).to_epsg()):
            raise DemError(
                f"COG CRS {src.crs} != out_crs {out_crs}; cross-CRS reprojection is not yet "
                "implemented (v1 MVP uses MRDEM native EPSG:3979)."
            )
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *bbox_4326, densify_pts=21)
        win = from_bounds(left, bottom, right, top, transform=src.transform)
        win = win.round_offsets().round_lengths()
        win = intersection(win, Window(0, 0, src.width, src.height))
        if win.width < 1 or win.height < 1:
            raise NoDemCoverageError(f"DEM does not overlap bbox {bbox_4326}.")

        data = src.read(window=win)
        win_transform = src.window_transform(win)
        meta = src.meta.copy()
        meta.update(height=int(win.height), width=int(win.width), transform=win_transform)
        with rasterio.open(out_path, "w", **meta) as dst:
            dst.write(data)

        b = window_bounds(win, src.transform)  # (left, bottom, right, top) in src/out CRS
        return (b[0], b[1], b[2], b[3])
