"""Auth-free live SoilSource: ISRIC SoilGrids global soil texture via the public WCS
(no login, no download). Fetches clay + sand (0-5 cm) for the AOI bbox and derives a
hydrologic-soil-group (HSG) raster, which acquire.soil clips and derive turns into CN.

This is the default soil source because it needs no credentials. HYSOGs250m (a purpose-built
HSG product) remains an optional override when its file is cached (Earthdata login).
"""
import io
import os
import tempfile

import numpy as np
import rasterio

from swmmcanada.acquire.soil import SoilAsset
from swmmcanada.sources import _http

_WCS = "https://maps.isric.org/mapserv?map=/map/{prop}.map"


def texture_to_hsg(clay_gkg, sand_gkg):
    """SoilGrids texture (g/kg) → HSG code (1=A, 2=B, 3=C, 4=D, 255=NoData).

    Simplified, documented USDA-style mapping by clay/sand percent: sandy low-clay soils
    infiltrate fast (A); heavy clay runs off (D); clayey loams are C; everything else is a
    loamy B. `derive` exposes the HSG→CN table, so this stays a defensible default.
    """
    clay = np.asarray(clay_gkg, dtype="float64") / 10.0  # g/kg → %
    sand = np.asarray(sand_gkg, dtype="float64") / 10.0
    hsg = np.full(clay.shape, 2, dtype="uint8")                 # B (loamy) default
    hsg = np.where((sand >= 50) & (clay < 10), 1, hsg)          # A: sandy, low clay
    hsg = np.where((clay >= 27) & (clay < 40), 3, hsg)          # C: clayey
    hsg = np.where(clay >= 40, 4, hsg)                          # D: heavy clay
    hsg = np.where((np.asarray(clay_gkg) <= 0) & (np.asarray(sand_gkg) <= 0), 255, hsg)
    return hsg.astype("uint8")


def texture_classes(clay_gkg, sand_gkg):
    """SoilGrids texture (g/kg) → USDA texture-class code raster (ADR 0013;
    codes = derive.infiltration.TEXTURE_CODE, 255 = NoData). Vectorised over the small
    class count: one boolean mask per class beats a per-pixel Python loop."""
    from swmmcanada.derive.infiltration import TEXTURE_CODE, usda_texture_class

    clay = np.asarray(clay_gkg, dtype="float64") / 10.0   # g/kg → %
    sand = np.asarray(sand_gkg, dtype="float64") / 10.0
    out = np.full(clay.shape, 255, dtype="uint8")
    valid = ~((np.asarray(clay_gkg) <= 0) & (np.asarray(sand_gkg) <= 0))
    # Classify the distinct (clay, sand) pairs actually present (SoilGrids tiles repeat
    # values heavily), then paint each class code back onto the raster.
    pairs = {(float(c), float(s)) for c, s in zip(clay[valid].ravel(), sand[valid].ravel())}
    for c, s in pairs:
        code = TEXTURE_CODE[usda_texture_class(c, s)]
        out[valid & (clay == c) & (sand == s)] = code
    return out


class SoilGridsSource:
    def __init__(self, timeout: float = 90.0, depth: str = "0-5cm"):
        self.timeout = timeout
        self.depth = depth

    def _wcs(self, prop, bbox):
        cov = f"{prop}_{self.depth}_mean"
        params = {
            "SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCoverage",
            "COVERAGEID": cov, "FORMAT": "image/tiff",
            "SUBSET": [f"Long({bbox[0]},{bbox[2]})", f"Lat({bbox[1]},{bbox[3]})"],
            "SUBSETTINGCRS": "http://www.opengis.net/def/crs/EPSG/0/4326",
            "OUTPUTCRS": "http://www.opengis.net/def/crs/EPSG/0/4326",
        }
        r = _http.request_with_retry("GET", _WCS.format(prop=prop), params=params, timeout=self.timeout)
        with rasterio.open(io.BytesIO(r.content)) as src:
            return src.read(1), src.profile

    def select(self, bbox_wgs84):
        clay, profile = self._wcs("clay", bbox_wgs84)
        sand, _ = self._wcs("sand", bbox_wgs84)
        hsg = texture_to_hsg(clay, sand)
        fd, path = tempfile.mkstemp(suffix="_soilgrids_hsg.tif")
        os.close(fd)
        profile = dict(profile)
        profile.update(dtype="uint8", count=1, nodata=255)
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(hsg, 1)
        # ADR 0013: the SAME clay/sand fetch also yields the USDA texture classes Green-Ampt
        # wants — classify per pixel and ship a second categorical raster instead of
        # discarding the texture once HSG is derived.
        fd, tex_path = tempfile.mkstemp(suffix="_soilgrids_texture.tif")
        os.close(fd)
        with rasterio.open(tex_path, "w", **profile) as dst:
            dst.write(texture_classes(clay, sand), 1)
        return SoilAsset(hsg_cog_href=path, crs="EPSG:4326", texture_cog_href=tex_path)
