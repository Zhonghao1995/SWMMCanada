"""Shared CRS seam: ONE place that picks the metric CRS for an AOI and builds lon/lat
projectors. Before this, pipeline and validate each recomputed the UTM-zone formula and
build wrapped its own Transformer — three copies of the same knowledge (2026-07 review)."""
from typing import Callable, Optional, Tuple


def utm_crs_for(aoi) -> str:
    """The UTM zone CRS (metres, northern hemisphere) covering the AOI's bbox centre —
    used for `.inp` display coordinates and metric geometry checks, so SWMM/PCSWMM render
    the model undistorted rather than as lon/lat degrees."""
    min_lon, _, max_lon, _ = aoi.bbox
    zone = int(((min_lon + max_lon) / 2 + 180) / 6) + 1
    return f"EPSG:{32600 + zone}"


def lonlat_projector(crs: Optional[str]) -> Callable[[float, float], Tuple[float, float]]:
    """A ``(lon, lat) -> (x, y)`` projector into ``crs``, or identity when ``crs`` is falsy
    (coordinates stay lon/lat). Accepts scalars or numpy arrays (pyproj passthrough)."""
    if not crs:
        return lambda x, y: (x, y)
    from pyproj import Transformer

    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return lambda x, y: tr.transform(x, y)
