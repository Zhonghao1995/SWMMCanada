"""The /api/v1/aoi/preview endpoint: parse a boundary without starting a build, so the
frontend can show the geometry + area (and any parse error) the moment a file is picked.
Errors must match a real submit exactly — same parsing path, same HTTP codes."""
import io
import zipfile

import geopandas as gpd
from fastapi.testclient import TestClient
from shapely.geometry import shape

from swmmcanada.api import create_app

OTTAWA = {
    "type": "Polygon",
    "coordinates": [
        [[-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]
    ],
}
OVERSIZE = {
    "type": "Polygon",
    "coordinates": [
        [[-75.74, 45.36], [-75.66, 45.36], [-75.66, 45.41], [-75.74, 45.41], [-75.74, 45.36]]
    ],
}


def _client(tmp_path):
    return TestClient(create_app(pipeline=lambda *a, **k: None, workdir=tmp_path, run_inline=True))


def _shapefile_zip_bytes(tmp_path) -> bytes:
    """A zipped shapefile of the Ottawa test polygon (with .prj), like a real upload."""
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[shape(OTTAWA)], crs="EPSG:4326")
    shp_dir = tmp_path / "shp"
    shp_dir.mkdir()
    gdf.to_file(shp_dir / "aoi.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in shp_dir.iterdir():
            z.write(f, f.name)
    return buf.getvalue()


def test_preview_polygon(tmp_path):
    r = _client(tmp_path).post("/api/v1/aoi/preview", data={"polygon": __import__("json").dumps(OTTAWA)})
    assert r.status_code == 200
    j = r.json()
    assert j["geometry"]["type"] == "Polygon"
    assert len(j["bbox"]) == 4 and j["bbox"][0] == -75.70
    assert 0 < j["area_km2"] < 25
    assert j["source"] == "geojson"


def test_preview_zipped_shapefile(tmp_path):
    data = _shapefile_zip_bytes(tmp_path)
    r = _client(tmp_path).post(
        "/api/v1/aoi/preview",
        files={"file": ("aoi.zip", data, "application/zip")},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["source"] == "shapefile"
    assert j["geometry"]["type"] in ("Polygon", "MultiPolygon")
    # bbox matches the drawn equivalent — same canonical AOI either way
    for got, want in zip(j["bbox"], (-75.70, 45.41, -75.68, 45.42)):
        assert abs(got - want) < 1e-6


def test_preview_oversize_is_413(tmp_path):
    r = _client(tmp_path).post(
        "/api/v1/aoi/preview", data={"polygon": __import__("json").dumps(OVERSIZE)})
    assert r.status_code == 413


def test_preview_garbage_is_422(tmp_path):
    c = _client(tmp_path)
    assert c.post("/api/v1/aoi/preview", data={}).status_code == 422        # nothing provided
    r = c.post("/api/v1/aoi/preview",
               files={"file": ("aoi.zip", b"not a zip", "application/zip")})
    assert r.status_code == 422                                             # unreadable upload
