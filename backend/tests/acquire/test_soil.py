"""TDD for acquire.soil: clip a (fixture) HSG COG to a bbox + emit HSG->CN table, offline.

Mirrors test_dem.py: synthesize a tiny categorical HSG-code GeoTIFF (EPSG:3979, values
1..4), inject a FakeSoilSource, and assert acquire_soil clips it and returns the CN table.
"""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from swmmcanada.acquire.soil import (
    SoilAsset,
    SoilError,
    SoilResult,
    acquire_soil,
)

FIXTURE_BBOX = (-75.70, 45.40, -75.66, 45.44)   # outer extent of the synthetic HSG raster
REQUEST_BBOX = (-75.69, 45.41, -75.67, 45.43)   # smaller AOI inside it


def _make_hsg(path, bbox_4326=FIXTURE_BBOX, res_m=250.0, crs="EPSG:3979"):
    """A tiny categorical HSG raster: byte codes 1..4 tiled across the grid, nodata=255."""
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox_4326, densify_pts=21)
    width = max(4, int((right - left) // res_m))
    height = max(4, int((top - bottom) // res_m))
    transform = from_origin(left, top, res_m, res_m)
    # Cycle codes 1,2,3,4 across columns so every HSG class appears.
    row = (np.arange(width) % 4 + 1).astype("uint8")
    data = np.tile(row, (height, 1))
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="uint8", crs=crs, transform=transform, nodata=255,
    ) as dst:
        dst.write(data, 1)
    return width, height


class FakeSoilSource:
    def __init__(self, href, crs="EPSG:3979"):
        self.href = href
        self.crs = crs

    def select(self, bbox_wgs84):
        return SoilAsset(hsg_cog_href=self.href, crs=self.crs)


class EmptySoilSource:
    def select(self, bbox_wgs84):
        return None


def test_acquire_soil_clips_to_bbox(tmp_path):
    cog = tmp_path / "hysogs_fixture.tif"
    fw, fh = _make_hsg(cog)
    res = acquire_soil(REQUEST_BBOX, tmp_path / "ws", source=FakeSoilSource(str(cog)))

    assert isinstance(res, SoilResult)
    assert res.hsg_raster.exists()
    assert res.hsg_raster.name == "hsg.tif"
    assert res.crs == "EPSG:3979"

    # HSG -> CN table contract.
    assert set(res.hsg_to_cn) == {"A", "B", "C", "D"}
    assert all(isinstance(v, int) for v in res.hsg_to_cn.values())
    # Monotonic: more-restrictive soils have higher CN.
    assert res.hsg_to_cn["A"] < res.hsg_to_cn["B"] < res.hsg_to_cn["C"] < res.hsg_to_cn["D"]

    with rasterio.open(res.hsg_raster) as out:
        assert out.width < fw and out.height < fh          # actually clipped
        assert out.crs.to_epsg() == 3979
        assert out.nodata == 255
        band = out.read(1)
        # Categorical values preserved (no resampling smear): only valid HSG codes present.
        assert set(np.unique(band)).issubset({1, 2, 3, 4, 255})


def test_degenerate_bbox_raises(tmp_path):
    cog = tmp_path / "hysogs_fixture.tif"
    _make_hsg(cog)
    with pytest.raises(SoilError):
        acquire_soil((-75.69, 45.42, -75.70, 45.41), tmp_path / "ws", source=FakeSoilSource(str(cog)))


def test_none_source_raises(tmp_path):
    with pytest.raises(SoilError):
        acquire_soil(REQUEST_BBOX, tmp_path / "ws", source=EmptySoilSource())


def test_crs_mismatch_raises(tmp_path):
    cog = tmp_path / "hysogs_fixture.tif"
    _make_hsg(cog, crs="EPSG:4326")  # not equal to out_crs EPSG:3979
    with pytest.raises(SoilError):
        acquire_soil(REQUEST_BBOX, tmp_path / "ws", source=FakeSoilSource(str(cog), crs="EPSG:4326"))
