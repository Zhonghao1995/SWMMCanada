"""acquire.landcover (spec 05): an AOI bbox → a single-band land-cover raster of integer
class codes clipped to that bbox, plus the class legend and a class→%impervious lookup.
Downstream, `derive` overlays this on the subcatchments (zonal stats) to set %Imperv.

The land-cover COG *discovery* (which ESRI ImageServer / source covers the bbox) is behind
an injected `LandcoverSource`, so source selection is testable offline; the windowed *clip*
is a pure rasterio op tested against a local fixture. Land-cover is CATEGORICAL, so the read
does a plain windowed slice (no resampling/interpolation — that would invent class codes).
v1 covers the MVP path: a source already in `out_crs` written out as `out_crs` (no
reprojection), mirroring dem.py's native-CRS MVP. Cross-CRS reprojection (nearest-neighbour
for categoricals) is a later increment.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol, Tuple

import rasterio
from rasterio.windows import Window, from_bounds, intersection
from rasterio.warp import transform_bounds

from swmmcanada.acquire.errors import AcquireError

Bbox = Tuple[float, float, float, float]


class LandcoverError(AcquireError):
    """acquire.landcover failure: degenerate bbox, no covering source, or an unreadable /
    cross-CRS COG (v1 requires the COG CRS to equal out_crs)."""


# --- Default NALCMS-style class→%impervious lookup (SWMMCanada-owned, versioned constant) --
# The %imperv column is a calibration starting point anchored to NLCD developed-class bands,
# NOT a measurement; `derive` exposes it as overridable. Source: spec 05 §3.5 (NRCan NALCMS
# 2020 Land Cover of Canada, 19-class). A default source attaches these alongside its raster.
LEGEND_VERSION = "nalcms-2020-v1"

DEFAULT_NALCMS_LEGEND: Dict[int, str] = {
    1: "Temperate/sub-polar needleleaf forest",
    2: "Sub-polar taiga needleleaf forest",
    3: "Tropical/sub-tropical broadleaf evergreen forest",
    4: "Tropical/sub-tropical broadleaf deciduous forest",
    5: "Temperate/sub-polar broadleaf deciduous forest",
    6: "Mixed forest",
    7: "Tropical/sub-tropical shrubland",
    8: "Temperate/sub-polar shrubland",
    9: "Tropical/sub-tropical grassland",
    10: "Temperate/sub-polar grassland",
    11: "Sub-polar/polar shrubland-lichen-moss",
    12: "Sub-polar/polar grassland-lichen-moss",
    13: "Sub-polar/polar barren-lichen-moss",
    14: "Wetland",
    15: "Cropland",
    16: "Barren lands",
    17: "Urban and built-up",
    18: "Water",
    19: "Snow and ice",
}

DEFAULT_NALCMS_IMPERVIOUS: Dict[int, float] = {
    1: 0.00, 2: 0.00, 3: 0.00, 4: 0.00, 5: 0.00,
    6: 0.00,                       # mixed forest
    7: 0.00, 8: 0.00,
    9: 0.02, 10: 0.02,             # grassland
    11: 0.00, 12: 0.00,
    13: 0.05,                      # barren-lichen-moss
    14: 0.00,                      # wetland
    15: 0.02,                      # cropland
    16: 0.05,                      # barren lands
    17: 0.70,                      # urban and built-up
    18: 0.00,                      # water
    19: 0.00,                      # snow and ice
}


@dataclass(frozen=True)
class LandcoverAsset:
    """A chosen land-cover COG to read, returned by a LandcoverSource."""
    cog_href: str               # a COG path/url of integer class codes
    crs: str                    # the COG's native CRS (e.g. "EPSG:3979")
    legend: Dict[int, str]      # class code -> human label
    impervious: Dict[int, float]  # class code -> impervious fraction in [0, 1]


@dataclass(frozen=True)
class LandcoverResult:
    raster_path: Path           # GeoTIFF of integer land-cover class codes, clipped to bbox
    crs: str                    # CRS of the written raster (default "EPSG:3979")
    legend: Dict[int, str]      # class code -> name
    impervious: Dict[int, float]  # class code -> impervious fraction in [0, 1]


class LandcoverSource(Protocol):
    """Selects the land-cover COG covering a WGS84 bbox. Production impl wraps the AAFC /
    NRCan ESRI ImageServers (`exportImage` by bbox); tests inject a fake returning a local
    fixture."""

    def select(self, bbox_wgs84: Bbox) -> Optional["LandcoverAsset"]:
        ...


def acquire_landcover(
    aoi_bbox_wgs84: Bbox,
    workspace: "Path | str",
    *,
    source: LandcoverSource,
    out_crs: str = "EPSG:3979",
) -> LandcoverResult:
    _validate_bbox(aoi_bbox_wgs84)
    asset = source.select(aoi_bbox_wgs84)
    if asset is None:
        raise LandcoverError(f"No land-cover source covers bbox {aoi_bbox_wgs84}.")

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    raster_path = ws / "landcover.tif"

    _clip_cog_to_bbox(asset.cog_href, aoi_bbox_wgs84, raster_path, out_crs)

    return LandcoverResult(
        raster_path=raster_path,
        crs=out_crs,
        legend=dict(asset.legend),
        impervious=dict(asset.impervious),
    )


# --- internals ---------------------------------------------------------------


def _validate_bbox(bbox: Bbox) -> None:
    minx, miny, maxx, maxy = bbox
    if not (minx < maxx and miny < maxy):
        raise LandcoverError(f"Degenerate AOI bbox: {bbox}")


def _clip_cog_to_bbox(href: str, bbox_4326: Bbox, out_path: Path, out_crs: str) -> None:
    """Windowed read of a categorical land-cover COG clipped to the bbox; write a GeoTIFF.
    No resampling: a plain window slice preserves the exact integer class codes (categorical
    data must never be bilinear/cubic-interpolated). v1 requires the COG CRS to equal out_crs
    (the AAFC/NALCMS native-CRS path)."""
    out_epsg = rasterio.crs.CRS.from_string(out_crs).to_epsg()
    with rasterio.open(href) as src:
        if src.crs is None or src.crs.to_epsg() != out_epsg:
            raise LandcoverError(
                f"Land-cover COG CRS {src.crs} != out_crs {out_crs}; cross-CRS reprojection "
                "is not yet implemented (v1 MVP uses the source's native CRS)."
            )
        left, bottom, right, top = transform_bounds(
            "EPSG:4326", src.crs, *bbox_4326, densify_pts=21
        )
        win = from_bounds(left, bottom, right, top, transform=src.transform)
        win = win.round_offsets().round_lengths()
        win = intersection(win, Window(0, 0, src.width, src.height))
        if win.width < 1 or win.height < 1:
            raise LandcoverError(f"Land cover does not overlap bbox {bbox_4326}.")

        data = src.read(window=win)
        win_transform = src.window_transform(win)
        meta = src.meta.copy()
        meta.update(height=int(win.height), width=int(win.width), transform=win_transform)
        with rasterio.open(out_path, "w", **meta) as dst:
            dst.write(data)
