"""Live DemSources: NRCan CanElevation HRDEM (LiDAR, 1–2 m) + an auto selector that
prefers HRDEM where it *truly* covers the AOI and falls back to MRDEM-30 (ADR 0010
follow-up; the acquire.dem seam anticipated this — HRDEM COGs share MRDEM's EPSG:3979,
so the windowed clip path is unchanged).

Discovery is the NRCan datacube STAC (`hrdem-lidar`: one item per LiDAR project, dtm/dsm
COGs on S3). An item's bbox can be far larger than its actual flight lines (a Vancouver
Island project spans ~6°), so bbox overlap alone is a lie: `AutoDemSource` verifies
coverage by a decimated windowed read of the COG itself before committing — an AOI inside
the bbox but outside the LiDAR would otherwise build on nodata.
"""
import re
from dataclasses import replace
from typing import Optional

from swmmcanada.acquire.dem import DemAsset
from swmmcanada.sources.dem_nrcan import NRCanDemSource

STAC_SEARCH_URL = "https://datacube.services.geo.ca/stac/api/search"
HRDEM_COLLECTION = "hrdem-lidar"


class HrdemLidarSource:
    """STAC discovery over `hrdem-lidar`; returns the most specific (smallest-bbox)
    project item whose bbox covers the AOI, or None. Coverage is left "partial" —
    only a read of the COG can prove the flight lines actually cover the AOI."""

    def __init__(self, search_url: str = STAC_SEARCH_URL, timeout_s: float = 30.0, search=None):
        self._url = search_url
        self._timeout = timeout_s
        self._search = search or self._search_stac   # injectable for offline tests

    def _search_stac(self, bbox_wgs84) -> list:
        from swmmcanada.sources import _http

        resp = _http.request_with_retry(
            "POST", self._url,
            json={"collections": [HRDEM_COLLECTION], "bbox": list(bbox_wgs84), "limit": 50},
            timeout=self._timeout,
        )
        return resp.json().get("features", [])

    def select(self, bbox_wgs84, prefer: str) -> Optional[DemAsset]:
        items = [f for f in self._search(bbox_wgs84) if (f.get("assets") or {}).get("dtm")]
        if not items:
            return None
        # Most specific project = smallest item bbox (projects nest inside province-wide ones).
        def _bbox_area(f):
            b = f.get("bbox") or [0, 0, 0, 0]
            return abs((b[2] - b[0]) * (b[3] - b[1]))

        item = min(items, key=_bbox_area)
        assets = item["assets"]
        return DemAsset(
            dtm_href=assets["dtm"]["href"],
            dsm_href=(assets.get("dsm") or {}).get("href"),
            source=f"hrdem-lidar:{item.get('id', '?')}",
            resolution_m=_resolution_from_id(item.get("id", "")),
            crs="EPSG:3979",
            item_ids=[item.get("id", "?")],
            coverage="partial",
        )


def _resolution_from_id(item_id: str) -> float:
    m = re.search(r"-(\d+)m$", item_id)
    return float(m.group(1)) if m else 1.0


class AutoDemSource:
    """HRDEM where a sampled read proves it covers the AOI; MRDEM-30 otherwise. Any
    discovery/read failure degrades to MRDEM — the DEM source must never kill a build."""

    def __init__(self, hrdem: Optional[HrdemLidarSource] = None, mrdem=None,
                 min_valid_fraction: float = 0.7, sample_px: int = 64):
        self._hrdem = hrdem or HrdemLidarSource()
        self._mrdem = mrdem or NRCanDemSource()
        self.min_valid_fraction = min_valid_fraction
        self.sample_px = sample_px

    def select(self, bbox_wgs84, prefer: str) -> Optional[DemAsset]:
        try:
            asset = self._hrdem.select(bbox_wgs84, prefer)
            if asset is not None:
                frac = _valid_fraction(asset.dtm_href, bbox_wgs84, self.sample_px)
                if frac >= self.min_valid_fraction:
                    return replace(asset, coverage=("full" if frac >= 0.99 else "partial"))
        except Exception:  # noqa: BLE001 — degrade to the national fallback, never raise
            pass
        return self._mrdem.select(bbox_wgs84, prefer)


def _valid_fraction(href: str, bbox_wgs84, sample_px: int) -> float:
    """Fraction of non-nodata cells in a decimated read of the COG over the bbox — the
    cheap ground truth that the LiDAR flight lines actually cover this AOI."""
    import numpy as np
    import rasterio
    from rasterio.windows import Window, from_bounds
    from rasterio.warp import transform_bounds

    with rasterio.open(href) as src:
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *bbox_wgs84, densify_pts=21)
        win = from_bounds(left, bottom, right, top, src.transform).intersection(
            Window(0, 0, src.width, src.height))
        if win.width < 1 or win.height < 1:
            return 0.0
        scale = max(1.0, max(win.width, win.height) / float(sample_px))
        out = (max(1, int(win.height / scale)), max(1, int(win.width / scale)))
        data = src.read(1, window=win, out_shape=out)
        if src.nodata is None:
            return 1.0
        return float(np.mean(data != src.nodata))
