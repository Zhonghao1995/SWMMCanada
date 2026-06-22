"""TDD for HysogsSoilSource: point acquire.soil at a local HYSOGs-style EPSG:4326 raster
(codes 1-4) and confirm it clips to the AOI. Offline."""
import numpy as np
import rasterio
from rasterio.transform import from_origin

from swmmcanada.acquire.soil import acquire_soil
from swmmcanada.sources.soil_hysogs import HysogsSoilSource


def _make_hysogs(path, bbox=(-75.72, 45.40, -75.66, 45.44), res_deg=0.0025):
    minx, miny, maxx, maxy = bbox
    w = int((maxx - minx) / res_deg)
    h = int((maxy - miny) / res_deg)
    transform = from_origin(minx, maxy, res_deg, res_deg)
    row = np.tile(np.array([1, 2, 3, 4], dtype="uint8"), w // 4 + 1)[:w]
    data = np.tile(row, (h, 1))
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype="uint8", crs="EPSG:4326", transform=transform, nodata=255,
    ) as dst:
        dst.write(data, 1)


def test_hysogs_source_clips_local_file(tmp_path):
    hy = tmp_path / "HYSOGs250m.tif"
    _make_hysogs(str(hy))
    res = acquire_soil((-75.70, 45.41, -75.68, 45.42), tmp_path / "ws",
                       source=HysogsSoilSource(str(hy)), out_crs="EPSG:4326")
    assert res.hsg_raster.exists()
    assert res.crs == "EPSG:4326"
    assert set("ABCD").issubset(res.hsg_to_cn)
    with rasterio.open(res.hsg_raster) as r:
        vals = {int(v) for v in np.unique(r.read(1))}
    assert vals & {1, 2, 3, 4}   # real HSG codes survive the clip


def test_missing_file_returns_none(tmp_path):
    assert HysogsSoilSource(str(tmp_path / "absent.tif")).select((-75.7, 45.4, -75.6, 45.5)) is None
