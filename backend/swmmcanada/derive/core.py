"""derive.core (spec 07 §3): turn already-acquired/clipped DEM + land-cover + soil(HSG)
rasters plus `network`'s subcatchment polygons into REAL SWMM subcatchment parameters,
overwriting the placeholder `pct_imperv` / `cn` / `pct_slope` carried by `SubcatchmentIn`.

This stage is pure computation and fully offline: every input has already been acquired
and clipped by `acquire.dem / acquire.landcover / acquire.soil`. For each subcatchment that
carries a polygon (a list of (lon, lat) WGS84 coords), we:

  * build a shapely polygon (EPSG:4326), reproject it into each raster's CRS (pyproj),
    and mask the raster to the polygon (rasterio.mask.mask, all_touched);
  * %imperv  = 100 * area-weighted mean of the land-cover impervious fraction over the
    masked land-cover pixels;
  * cn       = hsg_to_cn[<dominant HSG letter>], dominant = majority HSG code in the
    polygon mapped 1->A, 2->B, 3->C, 4->D (HYSOGs dual codes 11-14 reduced to A-D);
  * %slope   = mean terrain slope (percent rise) within the polygon, computed from the
    DEM via numpy.gradient / pixel size.

If a layer's polygon overlap is empty (no valid pixels), the corresponding existing value
on the subcatchment is kept (never crash, never emit NaN). Subcatchments without a polygon
are returned unchanged. A NEW list is returned (dataclasses.replace), preserving
name / outlet_node / width_m / area_ha / polygon.
"""
from dataclasses import replace
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.mask import mask as rio_mask
from shapely.geometry import mapping
from shapely.geometry import shape as shp_shape
from shapely.ops import transform as shp_transform

from swmmcanada.acquire.landcover import LandcoverResult
from swmmcanada.acquire.soil import SoilResult
from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.derive import infiltration

# HYSOGs250m code -> HSG letter. Dual (shallow-water-table) codes 11-14 reduce to their
# DRAINED group by default (A/D->A, B/D->B, C/D->C, D/D->D); spec 07 §3.0.
_HSG_CODE_TO_LETTER = {
    1: "A", 2: "B", 3: "C", 4: "D",
    11: "A", 12: "B", 13: "C", 14: "D",
}


class DeriveError(Exception):
    """Base for derive failures. (derive is offline; raised only on malformed inputs,
    e.g. a raster missing CRS metadata.)"""


def derive_parameters(
    subcatchments: List[SubcatchmentIn],
    dem_path: "Path | str",
    landcover: LandcoverResult,
    soil: SoilResult,
) -> List[SubcatchmentIn]:
    """Compute real SWMM parameters per subcatchment, overwriting placeholders.

    Returns a NEW list of SubcatchmentIn. Subcatchments with `polygon is None` are passed
    through unchanged. Empty raster overlap keeps the subcatchment's existing value.
    """
    out: List[SubcatchmentIn] = []
    for sub in subcatchments:
        if not sub.polygon:
            out.append(sub)
            continue

        poly_4326 = shp_shape(
            {"type": "Polygon", "coordinates": [[(float(lon), float(lat)) for lon, lat in sub.polygon]]}
        )

        pct_imperv = _impervious_pct(poly_4326, landcover, fallback=sub.pct_imperv)
        pct_slope = _mean_slope_pct(poly_4326, dem_path, fallback=sub.pct_slope)

        # Infiltration superset (ADR 0013): ONE zonal pass gives the dominant HSG, from
        # which all three parameter sets derive; Green-Ampt prefers the real texture
        # raster when the soil source shipped one (SoilGrids), else the HSG tier.
        letter = _dominant_hsg(poly_4326, soil)
        cn = _curve_number(letter, soil, fallback=sub.cn, poly_4326=poly_4326,
                           landcover=landcover)
        f0, fc, decay = infiltration.horton_for_hsg(letter)
        texture = _dominant_texture(poly_4326, soil)
        psi, ksat, imd = (infiltration.green_ampt_for_texture(texture) if texture
                          else infiltration.green_ampt_for_hsg(letter))

        out.append(replace(
            sub, pct_imperv=pct_imperv, cn=cn, pct_slope=pct_slope,
            horton_f0_mm_h=f0, horton_fc_mm_h=fc, horton_decay_1_h=decay,
            ga_psi_mm=psi, ga_ksat_mm_h=ksat, ga_imd=imd,
        ))
    return out


# --- per-layer zonal stats ----------------------------------------------------


def _impervious_pct(poly_4326, landcover: LandcoverResult, *, fallback: float) -> float:
    """Area-weighted mean impervious fraction (x100) over masked land-cover pixels."""
    with rasterio.open(landcover.raster_path) as src:
        data, _ = _mask_to_polygon(src, poly_4326)
        if data is None:
            return fallback
        nodata = src.nodata

    flat = data.ravel()
    if nodata is not None:
        flat = flat[flat != nodata]
    if flat.size == 0:
        return fallback

    imperv_lookup = landcover.impervious
    total = 0.0
    count = 0
    for code in np.unique(flat):
        n = int(np.count_nonzero(flat == code))
        frac = imperv_lookup.get(int(code), 0.0)  # unknown class -> impervious 0 (spec §5)
        total += n * frac
        count += n
    if count == 0:
        return fallback
    return float(100.0 * total / count)


def _majority_code(raster_path, poly_4326, decodable: set) -> Optional[int]:
    """Majority categorical code within the polygon, ignoring nodata and undecodable codes."""
    with rasterio.open(raster_path) as src:
        data, _ = _mask_to_polygon(src, poly_4326)
        if data is None:
            return None
        nodata = src.nodata

    flat = data.ravel()
    if nodata is not None:
        flat = flat[flat != nodata]
    keep = np.array([c for c in flat if int(c) in decodable], dtype=flat.dtype)
    if keep.size == 0:
        return None
    codes, counts = np.unique(keep, return_counts=True)
    return int(codes[int(np.argmax(counts))])


def _dominant_hsg(poly_4326, soil: SoilResult) -> Optional[str]:
    """Dominant (majority) HSG letter within the polygon, or None outside coverage."""
    code = _majority_code(soil.hsg_raster, poly_4326, set(_HSG_CODE_TO_LETTER))
    return _HSG_CODE_TO_LETTER[code] if code is not None else None


def _dominant_texture(poly_4326, soil: SoilResult) -> Optional[str]:
    """Dominant USDA texture class within the polygon (ADR 0013), when the soil source
    published a texture raster; None otherwise."""
    if not soil.texture_raster:
        return None
    code = _majority_code(soil.texture_raster, poly_4326, set(infiltration.CODE_TEXTURE))
    return infiltration.CODE_TEXTURE[code] if code is not None else None


# TR-55 curve numbers by cover CATEGORY x hydrologic soil group (F-021/ADR 0024): SCS CN
# depends on land use AND soils; HSG alone treated every cover like one blanket value.
# Categories aggregate NALCMS classes (legend in acquire.landcover); values are the
# standard TR-55 Table 2-2 rows documented in ASSUMPTIONS.md.
_CN_TABLE = {
    "forest":   {"A": 30.0, "B": 55.0, "C": 70.0, "D": 77.0},   # woods, fair-good
    "shrub":    {"A": 35.0, "B": 56.0, "C": 70.0, "D": 77.0},   # brush, fair
    "grass":    {"A": 39.0, "B": 61.0, "C": 74.0, "D": 80.0},   # open space, fair
    "crop":     {"A": 67.0, "B": 78.0, "C": 85.0, "D": 89.0},   # row crop, SR good
    "wetland":  {"A": 85.0, "B": 85.0, "C": 85.0, "D": 85.0},   # saturated ground
    "barren":   {"A": 77.0, "B": 86.0, "C": 91.0, "D": 94.0},   # fallow/bare
    "built":    {"A": 89.0, "B": 92.0, "C": 94.0, "D": 95.0},   # commercial districts
    "water":    {"A": 98.0, "B": 98.0, "C": 98.0, "D": 98.0},   # direct runoff
}
_NALCMS_CATEGORY = {
    1: "forest", 2: "forest", 3: "forest", 4: "forest", 5: "forest", 6: "forest",
    7: "shrub", 8: "shrub", 11: "shrub",
    9: "grass", 10: "grass", 12: "grass", 13: "grass",
    14: "wetland", 15: "crop", 16: "barren", 17: "built", 18: "water", 19: "barren",
}


def _curve_number(letter: Optional[str], soil: SoilResult, *, fallback: float,
                  poly_4326=None, landcover: Optional[LandcoverResult] = None) -> float:
    """Area-weighted TR-55 CN over the cell's land-cover classes for the dominant HSG
    (F-021). Without a usable land-cover read this degrades to the old single HSG->CN
    lookup, and without that to the caller's fallback."""
    if letter and poly_4326 is not None and landcover is not None:
        fracs = _class_fractions(poly_4326, landcover)
        if fracs:
            hsg = letter if letter in ("A", "B", "C", "D") else "B"
            cn = sum(frac * _CN_TABLE[_NALCMS_CATEGORY.get(code, "grass")][hsg]
                     for code, frac in fracs.items())
            return round(float(cn), 1)
    cn = soil.hsg_to_cn.get(letter) if letter else None
    return float(cn) if cn is not None else fallback


def _class_fractions(poly_4326, landcover: LandcoverResult) -> dict:
    """{NALCMS class code: area fraction} over the cell's masked land-cover pixels."""
    with rasterio.open(landcover.raster_path) as src:
        data, _ = _mask_to_polygon(src, poly_4326)
        if data is None:
            return {}
        nodata = src.nodata
    flat = data.ravel()
    if nodata is not None:
        flat = flat[flat != nodata]
    if flat.size == 0:
        return {}
    return {int(code): float(np.count_nonzero(flat == code)) / flat.size
            for code in np.unique(flat)}


def _mean_slope_pct(poly_4326, dem_path: "Path | str", *, fallback: float) -> float:
    """Mean terrain slope (percent rise) within the polygon, from the DEM.

    Slope = 100 * |grad(z)| where grad is numpy.gradient over the masked DEM window,
    scaled by the pixel size (metres) of the (projected) DEM grid.
    """
    with rasterio.open(dem_path) as src:
        data, transform = _mask_to_polygon(src, poly_4326, fill=np.nan, as_float=True)
        if data is None:
            return fallback
        nodata = src.nodata

    band = data.astype("float64")
    if nodata is not None:
        band = np.where(band == float(nodata), np.nan, band)

    # Need at least a 2x2 valid window to take a gradient.
    if band.shape[0] < 2 or band.shape[1] < 2:
        return fallback
    if np.all(np.isnan(band)):
        return fallback

    px = abs(transform.a)  # pixel width (m)
    py = abs(transform.e)  # pixel height (m)
    if px <= 0 or py <= 0:
        return fallback

    # numpy.gradient returns (d/d_row, d/d_col) = (d/dy, d/dx).
    dzdy, dzdx = np.gradient(band, py, px)
    rise = np.sqrt(dzdx ** 2 + dzdy ** 2)
    slope_pct = 100.0 * rise
    valid = slope_pct[~np.isnan(slope_pct)]
    if valid.size == 0:
        return fallback
    return float(np.mean(valid))


# --- masking helper -----------------------------------------------------------


def _mask_to_polygon(
    src, poly_4326, *, fill=0, as_float: bool = False
) -> Tuple[Optional[np.ndarray], Optional[object]]:
    """Reproject `poly_4326` (EPSG:4326) into `src`'s CRS and mask the raster to it.

    Returns (band2d, window_transform) for the cropped masked window, or (None, None) if
    the polygon does not overlap the raster (no valid pixels). Uses all_touched=True so
    tiny polygons that cover < 1 pixel still pick up the pixels they intersect.
    """
    if src.crs is None:
        raise DeriveError(f"Raster {getattr(src, 'name', '?')} is missing CRS metadata.")

    poly_src = _reproject_polygon(poly_4326, "EPSG:4326", src.crs)
    geoms = [mapping(poly_src)]
    try:
        out, win_transform = rio_mask(
            src, geoms, crop=True, all_touched=True, filled=True,
            nodata=(np.nan if as_float else None),
        )
    except ValueError:
        # rasterio raises "Input shapes do not overlap raster." when there is no overlap.
        return None, None

    band = out[0]
    if band.size == 0:
        return None, None
    return band, win_transform


def _reproject_polygon(poly, src_crs: str, dst_crs):
    dst = dst_crs if isinstance(dst_crs, str) else CRS(dst_crs).to_string()
    if CRS.from_user_input(src_crs).to_epsg() == CRS.from_user_input(dst).to_epsg():
        return poly
    transformer = Transformer.from_crs(src_crs, dst, always_xy=True)
    return shp_transform(transformer.transform, poly)
