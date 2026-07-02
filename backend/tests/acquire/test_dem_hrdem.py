"""HRDEM LiDAR source + AutoDemSource (ADR 0010 follow-up), fully offline: STAC search is
injected; coverage sampling runs against local fixture COGs (full-data vs mostly-nodata)."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from swmmcanada.acquire.dem import DemAsset
from swmmcanada.sources.dem_hrdem import AutoDemSource, HrdemLidarSource, _resolution_from_id

BBOX = (-123.14, 49.27, -123.10, 49.29)   # downtown-Vancouver-ish


def _item(iid, bbox, with_dtm=True):
    assets = {"dsm": {"href": f"s3://{iid}-dsm.tif"}}
    if with_dtm:
        assets["dtm"] = {"href": f"s3://{iid}-dtm.tif"}
    return {"id": iid, "bbox": list(bbox), "assets": assets}


def _cog(path, nodata_fraction=0.0, crs="EPSG:3979", res=1.0):
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *BBOX, densify_pts=21)
    w = h = 64
    transform = from_origin(left, top, (right - left) / w, (top - bottom) / h)
    data = np.full((h, w), 100.0, dtype="float32")
    n_bad = int(nodata_fraction * h)
    if n_bad:
        data[:n_bad, :] = -32767.0
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs=crs, transform=transform, nodata=-32767.0) as dst:
        dst.write(data, 1)
    return path


# --- STAC discovery -------------------------------------------------------------


def test_select_picks_most_specific_project():
    huge = _item("BC-Vancouver_Island_2018-1m", (-128.7, 47.4, -122.2, 52.1))
    tight = _item("BC-Lower_Mainland_2016-1m", (-123.3, 49.0, -121.3, 49.5))
    src = HrdemLidarSource(search=lambda bbox: [huge, tight])

    asset = src.select(BBOX, "auto")
    assert asset.item_ids == ["BC-Lower_Mainland_2016-1m"]   # smallest bbox wins
    assert asset.source == "hrdem-lidar:BC-Lower_Mainland_2016-1m"
    assert asset.resolution_m == 1.0 and asset.crs == "EPSG:3979"
    assert asset.dtm_href.endswith("-dtm.tif")


def test_select_none_without_items_or_dtm():
    assert HrdemLidarSource(search=lambda bbox: []).select(BBOX, "auto") is None
    no_dtm = _item("X-2m", (-124, 49, -123, 50), with_dtm=False)
    assert HrdemLidarSource(search=lambda bbox: [no_dtm]).select(BBOX, "auto") is None


def test_resolution_parsed_from_item_id():
    assert _resolution_from_id("QC-Montreal_2020-2m") == 2.0
    assert _resolution_from_id("weird-id") == 1.0


# --- Auto selection with sampled coverage ----------------------------------------


class _StubHrdem:
    def __init__(self, asset=None, raise_=False):
        self._asset, self._raise = asset, raise_

    def select(self, bbox, prefer):
        if self._raise:
            raise RuntimeError("STAC down")
        return self._asset


class _StubMrdem:
    def select(self, bbox, prefer):
        return DemAsset(dtm_href="s3://mrdem.tif", dsm_href=None, source="mrdem-30",
                        resolution_m=30.0, crs="EPSG:3979", item_ids=["mrdem"],
                        coverage="fallback")


def _hrdem_asset(href):
    return DemAsset(dtm_href=str(href), dsm_href=None, source="hrdem-lidar:T",
                    resolution_m=1.0, crs="EPSG:3979", item_ids=["T"], coverage="partial")


def test_auto_prefers_hrdem_when_covered(tmp_path):
    cog = _cog(tmp_path / "full.tif", nodata_fraction=0.0)
    auto = AutoDemSource(hrdem=_StubHrdem(_hrdem_asset(cog)), mrdem=_StubMrdem())
    asset = auto.select(BBOX, "auto")
    assert asset.source.startswith("hrdem-lidar") and asset.coverage == "full"


def test_auto_falls_back_when_lidar_missing_over_aoi(tmp_path):
    cog = _cog(tmp_path / "holey.tif", nodata_fraction=0.8)   # bbox inside item, outside flight lines
    auto = AutoDemSource(hrdem=_StubHrdem(_hrdem_asset(cog)), mrdem=_StubMrdem())
    assert auto.select(BBOX, "auto").source == "mrdem-30"


def test_auto_falls_back_on_discovery_failure():
    auto = AutoDemSource(hrdem=_StubHrdem(raise_=True), mrdem=_StubMrdem())
    assert auto.select(BBOX, "auto").source == "mrdem-30"


def test_auto_falls_back_when_no_item():
    auto = AutoDemSource(hrdem=_StubHrdem(asset=None), mrdem=_StubMrdem())
    assert auto.select(BBOX, "auto").source == "mrdem-30"


# --- pipeline wiring (#51: auto IS the default; mrdem is the escape hatch) --------


def test_pipeline_default_is_auto(monkeypatch):
    from swmmcanada.pipeline import _dem_source_auto

    monkeypatch.delenv("SWMMCANADA_DEM_SOURCE", raising=False)
    assert isinstance(_dem_source_auto(None), AutoDemSource)


def test_pipeline_env_forces_mrdem(monkeypatch):
    from swmmcanada.pipeline import _dem_source_auto
    from swmmcanada.sources.dem_nrcan import NRCanDemSource

    monkeypatch.setenv("SWMMCANADA_DEM_SOURCE", "mrdem")
    assert isinstance(_dem_source_auto(None), NRCanDemSource)


def test_pipeline_explicit_source_always_wins(monkeypatch):
    from swmmcanada.pipeline import _dem_source_auto

    monkeypatch.setenv("SWMMCANADA_DEM_SOURCE", "auto")
    marker = object()
    assert _dem_source_auto(marker) is marker
