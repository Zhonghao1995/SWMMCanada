"""Live SoilSource: NASA HYSOGs250m — global hydrologic soil groups for curve-number
modelling (EPSG:4326; codes 1=A,2=B,3=C,4=D, dual 11-14, NoData 255).

The file (~545 MB) sits behind NASA Earthdata login, so it is downloaded ONCE and cached
locally (same pattern as HYDAT). This source just points acquire.soil at the local file;
acquire.soil windowed-reads only the AOI, and derive maps codes 1-4 / 11-14 to A/B/C/D.
"""
from pathlib import Path

from swmmcanada.acquire.soil import SoilAsset


class HysogsSoilSource:
    def __init__(self, hysogs_path):
        self.hysogs_path = str(hysogs_path)

    def select(self, bbox_wgs84):
        if not Path(self.hysogs_path).exists():
            return None
        return SoilAsset(hsg_cog_href=self.hysogs_path, crs="EPSG:4326")
