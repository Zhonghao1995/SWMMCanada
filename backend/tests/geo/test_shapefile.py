"""TDD tests for geo.aoi_from_shapefile — the drawn-vs-uploaded equivalence invariant
(spec 01 §6 test 1). Fixtures are generated in temp dirs (no committed binaries)."""
import math
import zipfile

import geopandas as gpd
import pytest
from shapely.geometry import box, shape

from swmmcanada.geo import AOI, aoi_from_geojson, aoi_from_shapefile
from swmmcanada.geo.errors import AOICRSUnknownError

OTTAWA = {
    "type": "Polygon",
    "coordinates": [
        [
            [-75.695, 45.400],
            [-75.682, 45.400],
            [-75.682, 45.418],
            [-75.695, 45.418],
            [-75.695, 45.400],
        ]
    ],
}


def _write_shp(tmp_path, geoms, crs="EPSG:4326", name="aoi"):
    gdf = gpd.GeoDataFrame({"id": list(range(len(geoms)))}, geometry=geoms, crs=crs)
    shp = tmp_path / f"{name}.shp"
    gdf.to_file(shp)
    return shp


def test_projected_shapefile_matches_drawn(tmp_path):
    """A shapefile of the same region, stored in a *projected* CRS (EPSG:3347), reprojects
    back to the same AOI a drawn polygon produces — proving input method is irrelevant."""
    ref = aoi_from_geojson(OTTAWA)
    proj = gpd.GeoSeries([shape(OTTAWA)], crs="EPSG:4326").to_crs("EPSG:3347")
    shp = _write_shp(tmp_path, list(proj.values), crs="EPSG:3347")
    aoi = aoi_from_shapefile(str(shp))
    assert isinstance(aoi, AOI)
    assert aoi.source == "shapefile"
    assert math.isclose(aoi.area_km2, ref.area_km2, rel_tol=1e-3)
    for got, want in zip(aoi.bbox, ref.bbox):
        assert abs(got - want) < 1e-4


def test_zipped_shapefile(tmp_path):
    ref = aoi_from_geojson(OTTAWA)
    _write_shp(tmp_path, [shape(OTTAWA)])
    zp = tmp_path / "aoi.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for f in tmp_path.glob("aoi.*"):
            if f.suffix != ".zip":
                z.write(f, f.name)
    aoi = aoi_from_shapefile(str(zp))
    assert math.isclose(aoi.area_km2, ref.area_km2, rel_tol=1e-6)


def test_no_prj_raises(tmp_path):
    _write_shp(tmp_path, [shape(OTTAWA)])
    prj = tmp_path / "aoi.prj"
    if prj.exists():
        prj.unlink()
    with pytest.raises(AOICRSUnknownError):
        aoi_from_shapefile(str(tmp_path / "aoi.shp"))


def test_multi_feature_dissolve(tmp_path):
    """Two adjacent boxes covering the Ottawa extent dissolve to one AOI = their union."""
    ref = aoi_from_geojson(OTTAWA)
    b1 = box(-75.695, 45.400, -75.689, 45.418)
    b2 = box(-75.689, 45.400, -75.682, 45.418)
    shp = _write_shp(tmp_path, [b1, b2])
    aoi = aoi_from_shapefile(str(shp))
    assert math.isclose(aoi.area_km2, ref.area_km2, rel_tol=1e-3)
