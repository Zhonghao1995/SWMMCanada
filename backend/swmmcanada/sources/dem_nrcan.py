"""Live DemSource: NRCan CanElevation MRDEM-30 (national, EPSG:3979). v1 always uses MRDEM
(its COG is a cloud-optimized GeoTIFF on S3 — acquire.dem windowed-reads only the AOI).
HRDEM selection is a later increment."""
from typing import Optional

from swmmcanada.acquire.dem import DemAsset

_MRDEM_DTM = "https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dtm.tif"
_MRDEM_DSM = "https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dsm.tif"


class NRCanDemSource:
    def select(self, bbox_wgs84, prefer: str) -> Optional[DemAsset]:
        return DemAsset(
            dtm_href=_MRDEM_DTM,
            dsm_href=_MRDEM_DSM,
            source="mrdem-30",
            resolution_m=30.0,
            crs="EPSG:3979",
            item_ids=["mrdem"],
            coverage="fallback",
        )
