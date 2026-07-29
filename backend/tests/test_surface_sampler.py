"""Offline tests for pipeline._make_surface_sampler — the DEM-backed sampler the pipeline
injects into base.SURFACE_SAMPLER so rim-less cities get terrain-anchored inverts.
Fixture-COG approach mirrors tests/acquire/test_dem.py."""
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from swmmcanada.acquire.dem import DemAsset
from swmmcanada.pipeline import _make_surface_sampler

FIXTURE_BBOX = (-75.70, 45.40, -75.66, 45.44)   # outer extent of the synthetic DEM
REQUEST_BBOX = (-75.69, 45.41, -75.67, 45.43)   # smaller AOI inside it
NODATA = -32767.0


def _make_dem(path, bbox_4326=FIXTURE_BBOX, res_m=30.0, crs="EPSG:3979"):
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox_4326, densify_pts=21)
    width = max(2, int((right - left) // res_m))
    height = max(2, int((top - bottom) // res_m))
    transform = from_origin(left, top, res_m, res_m)
    data = np.full((height, width), 95.0, dtype="float32")
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float32", crs=crs, transform=transform, nodata=NODATA,
    ) as dst:
        dst.write(data, 1)


class FakeDemSource:
    def __init__(self, href):
        self.href = href

    def select(self, bbox_wgs84, prefer):
        return DemAsset(
            dtm_href=self.href, dsm_href=None, source="mrdem-30",
            resolution_m=30.0, crs="EPSG:3979", item_ids=["mrdem"], coverage="fallback",
        )


class EmptyDemSource:
    def select(self, bbox_wgs84, prefer):
        return None       # no coverage (e.g. an AOI outside Canada)


def test_samples_elevation_inside_dem(tmp_path):
    cog = tmp_path / "fixture.tif"
    _make_dem(cog)
    sample = _make_surface_sampler(REQUEST_BBOX, tmp_path / "ws", FakeDemSource(str(cog)))
    inside = (-75.68, 45.42)                       # centre of the request bbox
    outside = (-75.20, 45.42)                      # far outside the DEM extent
    got = sample([inside, outside, inside])
    assert got[0] == 95.0 and got[2] == 95.0
    assert got[1] is None                          # nodata / out of bounds -> None


def test_degrades_to_none_when_no_coverage(tmp_path):
    sample = _make_surface_sampler(REQUEST_BBOX, tmp_path / "ws", EmptyDemSource())
    got = sample([(-75.68, 45.42), (-75.68, 45.415)])
    assert got == [None, None]                     # sampler is best-effort by contract
