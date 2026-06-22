"""acquire.soil (spec 06): an AOI bbox -> a hydrologic soil group (HSG) raster clipped to
that bbox, plus a defensible HSG -> SCS curve-number lookup, for the infiltration path.

The HSG COG *discovery* is behind an injected `SoilSource` (so selection is testable
offline); the windowed *clip* is a pure rasterio op tested against a local fixture. This is
the MVP slice: a HYSOGs-style categorical raster already in EPSG:3979, window-read with
**nearest** resampling (categorical codes must not be interpolated) and written out as
EPSG:3979 (no reprojection — like dem.py, cross-CRS is a later increment).

Error classes are defined locally so this module owns its taxonomy.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol, Tuple

import rasterio
from rasterio.crs import CRS
from rasterio.windows import Window, from_bounds, intersection
from rasterio.warp import transform_bounds

Bbox = Tuple[float, float, float, float]

# Default urban HSG -> SCS curve number table (TR-55 / SCS, ARC II). A single representative
# CN per hydrologic soil group for the MVP urban path; `derive` refines this against land
# cover. Returned in SoilResult.hsg_to_cn for provenance/reproducibility.
DEFAULT_HSG_TO_CN: Dict[str, int] = {"A": 77, "B": 85, "C": 90, "D": 92}


class SoilError(Exception):
    """Base for acquire.soil errors (degenerate bbox, no coverage, unsupported CRS)."""


@dataclass(frozen=True)
class SoilResult:
    hsg_raster: Path            # GeoTIFF of HSG codes (1=A, 2=B, 3=C, 4=D), clipped to bbox
    crs: str                   # CRS of the written raster (default "EPSG:3979")
    hsg_to_cn: Dict[str, int]  # {"A": .., "B": .., "C": .., "D": ..} default urban CNs


@dataclass(frozen=True)
class SoilAsset:
    """A chosen HSG COG to read, returned by a SoilSource."""
    hsg_cog_href: str
    crs: str                   # the COG's native CRS (e.g. "EPSG:3979")


class SoilSource(Protocol):
    """Selects the HSG COG covering a WGS84 bbox. Production impl wraps a cached HYSOGs250m
    COG; tests inject a fake returning a local fixture."""

    def select(self, bbox_wgs84: Bbox) -> Optional["SoilAsset"]:
        ...


def acquire_soil(
    aoi_bbox_wgs84: Bbox,
    workspace: "Path | str",
    *,
    source: SoilSource,
    out_crs: str = "EPSG:3979",
) -> SoilResult:
    _validate_bbox(aoi_bbox_wgs84)
    asset = source.select(aoi_bbox_wgs84)
    if asset is None:
        raise SoilError(f"No soil/HSG source covers bbox {aoi_bbox_wgs84}.")

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)

    hsg_path = ws / "hsg.tif"
    _clip_cog_to_bbox(asset.hsg_cog_href, aoi_bbox_wgs84, hsg_path, out_crs)

    return SoilResult(
        hsg_raster=hsg_path,
        crs=out_crs,
        hsg_to_cn=dict(DEFAULT_HSG_TO_CN),
    )


# --- internals ---------------------------------------------------------------


def _validate_bbox(bbox: Bbox) -> None:
    minx, miny, maxx, maxy = bbox
    if not (minx < maxx and miny < maxy):
        raise SoilError(f"Degenerate AOI bbox: {bbox}")


def _clip_cog_to_bbox(href: str, bbox_4326: Bbox, out_path: Path, out_crs: str) -> None:
    """Windowed read of a categorical HSG COG clipped to the bbox; write a GeoTIFF. v1 requires
    the COG CRS to equal out_crs. HSG codes are categorical, so the window read is an exact
    pixel slice (nearest) — no resampling/interpolation that would invent codes."""
    with rasterio.open(href) as src:
        if src.crs is None or (src.crs.to_epsg() != CRS.from_string(out_crs).to_epsg()):
            raise SoilError(
                f"HSG COG CRS {src.crs} != out_crs {out_crs}; cross-CRS reprojection is not yet "
                "implemented (v1 MVP uses a COG already in out_crs)."
            )
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *bbox_4326, densify_pts=21)
        win = from_bounds(left, bottom, right, top, transform=src.transform)
        win = win.round_offsets().round_lengths()
        win = intersection(win, Window(0, 0, src.width, src.height))
        if win.width < 1 or win.height < 1:
            raise SoilError(f"HSG raster does not overlap bbox {bbox_4326}.")

        # Categorical read: exact pixel slice (no resampling) preserves HSG codes & nodata.
        data = src.read(window=win)
        win_transform = src.window_transform(win)
        meta = src.meta.copy()
        meta.update(height=int(win.height), width=int(win.width), transform=win_transform)
        with rasterio.open(out_path, "w", **meta) as dst:
            dst.write(data)
