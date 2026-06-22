"""Stand-in SoilSource: a uniform hydrologic soil group (default B) raster over the AOI.

This is a documented placeholder until a real HSG adapter is wired. The natural source,
HYSOGs250m, sits behind NASA Earthdata auth; AAFC Soil Landscapes of Canada (SLC) is
auth-free but vector (HSG must be derived from drainage class) — both are follow-ups.
Until then this keeps the infiltration path complete with a defensible default HSG.
"""
import os
import tempfile

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from swmmcanada.acquire.soil import SoilAsset


class ConstantHsgSoilSource:
    def __init__(self, hsg_code: int = 2, res_m: float = 30.0):  # 2 = HSG B
        self.hsg_code = hsg_code
        self.res_m = res_m

    def select(self, bbox_wgs84):
        left, bottom, right, top = transform_bounds("EPSG:4326", "EPSG:3979", *bbox_wgs84, densify_pts=21)
        width = max(4, int((right - left) / self.res_m))
        height = max(4, int((top - bottom) / self.res_m))
        fd, path = tempfile.mkstemp(suffix="_hsg.tif")
        os.close(fd)
        transform = from_origin(left, top, self.res_m, self.res_m)
        data = np.full((height, width), self.hsg_code, dtype="uint8")
        with rasterio.open(
            path, "w", driver="GTiff", height=height, width=width, count=1,
            dtype="uint8", crs="EPSG:3979", transform=transform, nodata=255,
        ) as dst:
            dst.write(data, 1)
        return SoilAsset(hsg_cog_href=path, crs="EPSG:3979")
