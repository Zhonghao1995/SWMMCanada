"""TDD for acquire.landcover: clip a (fixture) categorical land-cover COG to a bbox,
offline (spec 05 §6). A class-code GeoTIFF in EPSG:3979 is synthesised in a tmp dir and a
FakeLandcoverSource hands it to acquire_landcover; we assert it is clipped, written, and
carries the legend + impervious tables. No live network."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from swmmcanada.acquire.landcover import (
    DEFAULT_NALCMS_IMPERVIOUS,
    DEFAULT_NALCMS_LEGEND,
    LandcoverAsset,
    LandcoverError,
    LandcoverResult,
    acquire_landcover,
)

FIXTURE_BBOX = (-75.70, 45.40, -75.66, 45.44)   # outer extent of the synthetic land-cover tile
REQUEST_BBOX = (-75.69, 45.41, -75.67, 45.43)   # smaller AOI inside it


def _make_landcover(path, bbox_4326=FIXTURE_BBOX, res_m=30.0, crs="EPSG:3979"):
    """Synthesise a tiny single-band uint8 categorical raster of NALCMS-style class codes."""
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox_4326, densify_pts=21)
    width = max(4, int((right - left) // res_m))
    height = max(4, int((top - bottom) // res_m))
    transform = from_origin(left, top, res_m, res_m)
    rng = np.random.default_rng(0)
    # a deterministic mix of urban(17), water(18), forest(6), cropland(15)
    data = rng.choice(np.array([6, 15, 17, 18], dtype="uint8"), size=(height, width))
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="uint8", crs=crs, transform=transform, nodata=0,
    ) as dst:
        dst.write(data, 1)
    return width, height


class FakeLandcoverSource:
    def __init__(self, href, crs="EPSG:3979"):
        self.href = href
        self.crs = crs

    def select(self, bbox_wgs84):
        return LandcoverAsset(
            cog_href=self.href,
            crs=self.crs,
            legend=dict(DEFAULT_NALCMS_LEGEND),
            impervious=dict(DEFAULT_NALCMS_IMPERVIOUS),
        )


class EmptyLandcoverSource:
    def select(self, bbox_wgs84):
        return None


def test_acquire_landcover_clips_to_bbox(tmp_path):
    cog = tmp_path / "fixture.tif"
    fw, fh = _make_landcover(cog)
    res = acquire_landcover(REQUEST_BBOX, tmp_path / "ws", source=FakeLandcoverSource(str(cog)))

    assert isinstance(res, LandcoverResult)
    assert res.raster_path.exists()
    assert res.raster_path.name == "landcover.tif"
    assert res.crs == "EPSG:3979"

    # legend + impervious travel with the result and are sane
    assert res.legend[17] == DEFAULT_NALCMS_LEGEND[17]
    assert res.impervious[18] == 0.0          # water
    assert res.impervious[6] == 0.0           # forest
    assert res.impervious[17] == pytest.approx(0.70)   # urban/built-up
    assert all(0.0 <= v <= 1.0 for v in res.impervious.values())

    with rasterio.open(res.raster_path) as out:
        assert out.width < fw and out.height < fh      # actually clipped
        assert out.crs.to_epsg() == 3979
        assert out.count == 1
        # categorical: only class codes that existed in the fixture appear (no interpolation)
        present = set(np.unique(out.read(1)))
        assert present.issubset({0, 6, 15, 17, 18})


def test_degenerate_bbox_raises(tmp_path):
    cog = tmp_path / "fixture.tif"
    _make_landcover(cog)
    with pytest.raises(LandcoverError):
        acquire_landcover(
            (-75.69, 45.42, -75.70, 45.41), tmp_path / "ws",
            source=FakeLandcoverSource(str(cog)),
        )


def test_none_source_raises(tmp_path):
    with pytest.raises(LandcoverError):
        acquire_landcover(REQUEST_BBOX, tmp_path / "ws", source=EmptyLandcoverSource())
