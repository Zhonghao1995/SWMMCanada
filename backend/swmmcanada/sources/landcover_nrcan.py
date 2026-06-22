"""Live LandcoverSource: NRCan "Land Cover of Canada" (NALCMS 19-class) via the geo.ca STAC.
The classification COG is a public S3 cloud-optimized GeoTIFF in EPSG:3979 — acquire.landcover
windowed-reads only the AOI."""
import requests

from swmmcanada.acquire.landcover import (
    DEFAULT_NALCMS_IMPERVIOUS,
    DEFAULT_NALCMS_LEGEND,
    LandcoverAsset,
)

_STAC_SEARCH = "https://datacube.services.geo.ca/stac/api/search"


class NRCanLandcoverSource:
    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout

    def select(self, bbox_wgs84):
        r = requests.get(
            _STAC_SEARCH,
            params={"collections": "landcover", "bbox": ",".join(map(str, bbox_wgs84)), "limit": 10},
            timeout=self.timeout,
        )
        r.raise_for_status()
        feats = r.json().get("features", [])
        if not feats:
            return None
        feats.sort(key=lambda f: f.get("properties", {}).get("datetime", ""), reverse=True)
        feat = feats[0]
        href = (feat.get("assets", {}).get("classification") or {}).get("href")
        epsg = feat.get("properties", {}).get("proj:epsg")
        if not href or not epsg:
            return None
        return LandcoverAsset(
            cog_href=href,
            crs=f"EPSG:{epsg}",
            legend=dict(DEFAULT_NALCMS_LEGEND),
            impervious=dict(DEFAULT_NALCMS_IMPERVIOUS),
        )
