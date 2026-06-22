"""TDD for acquire.dem: clip a (fixture) COG to a bbox, offline (spec 04 §6)."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from swmmcanada.acquire.dem import DemAsset, DemResult, acquire_dem
from swmmcanada.acquire.errors import DegenerateBboxError, NoDemCoverageError

FIXTURE_BBOX = (-75.70, 45.40, -75.66, 45.44)   # outer extent of the synthetic DEM
REQUEST_BBOX = (-75.69, 45.41, -75.67, 45.43)   # smaller AOI inside it


def _make_dem(path, bbox_4326=FIXTURE_BBOX, res_m=30.0, crs="EPSG:3979"):
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox_4326, densify_pts=21)
    width = max(2, int((right - left) // res_m))
    height = max(2, int((top - bottom) // res_m))
    transform = from_origin(left, top, res_m, res_m)
    data = np.tile(np.linspace(90.0, 110.0, width, dtype="float32"), (height, 1))
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float32", crs=crs, transform=transform,
    ) as dst:
        dst.write(data, 1)
    return width, height


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
        return None


def test_acquire_dem_clips_to_bbox(tmp_path):
    cog = tmp_path / "fixture.tif"
    fw, fh = _make_dem(cog)
    res = acquire_dem(REQUEST_BBOX, tmp_path / "ws", source=FakeDemSource(str(cog)))

    assert isinstance(res, DemResult)
    assert res.path.exists()
    assert res.crs == "EPSG:3979"
    assert res.source == "mrdem-30" and res.resolution_m == 30.0
    assert res.item_ids == ["mrdem"]

    with rasterio.open(res.path) as out:
        assert out.width < fw and out.height < fh          # actually clipped
        assert out.crs.to_epsg() == 3979
    expected = transform_bounds("EPSG:4326", "EPSG:3979", *REQUEST_BBOX, densify_pts=21)
    for got, want in zip(res.bbox, expected):
        assert abs(got - want) < 60                         # within ~2 px (30 m)


def test_degenerate_bbox_raises(tmp_path):
    cog = tmp_path / "fixture.tif"
    _make_dem(cog)
    with pytest.raises(DegenerateBboxError):
        acquire_dem((-75.69, 45.42, -75.70, 45.41), tmp_path / "ws", source=FakeDemSource(str(cog)))


def test_no_coverage_raises(tmp_path):
    with pytest.raises(NoDemCoverageError):
        acquire_dem(REQUEST_BBOX, tmp_path / "ws", source=EmptyDemSource())
