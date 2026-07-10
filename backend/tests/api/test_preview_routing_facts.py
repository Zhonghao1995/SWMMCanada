"""Preview reports the routing facts a submit would use (mode/city/in_canada).

The aiswmm integration polls a multi-minute build blind; these fields let the
client announce "this AOI gets Ottawa's real network" BEFORE submitting, and
reroute non-Canadian AOIs without burning a task. ``mode`` must come from the
same dispatcher submit uses so the two can never disagree.
"""
import json

from fastapi.testclient import TestClient

from swmmcanada.api import create_app

OTTAWA = {
    "type": "Polygon",
    "coordinates": [
        [[-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]
    ],
}
RURAL_SK = {
    "type": "Polygon",
    "coordinates": [
        [[-106.10, 52.10], [-106.08, 52.10], [-106.08, 52.12], [-106.10, 52.12], [-106.10, 52.10]]
    ],
}
TOKYO = {
    "type": "Polygon",
    "coordinates": [
        [[139.60, 35.60], [139.62, 35.60], [139.62, 35.62], [139.60, 35.62], [139.60, 35.60]]
    ],
}


def _preview(tmp_path, polygon):
    client = TestClient(create_app(pipeline=lambda *a, **k: None, workdir=tmp_path, run_inline=True))
    r = client.post("/api/v1/aoi/preview", data={"polygon": json.dumps(polygon)})
    assert r.status_code == 200
    return r.json()


def test_city_aoi_reports_real_network_mode(tmp_path):
    j = _preview(tmp_path, OTTAWA)
    assert j["city"] == "ottawa"
    assert j["in_canada"] is True
    assert "Real municipal network" in j["mode"]
    assert "Ottawa" in j["mode"]


def test_non_city_canadian_aoi_reports_synthesis(tmp_path):
    j = _preview(tmp_path, RURAL_SK)
    assert j["city"] is None
    assert j["in_canada"] is True
    assert j["mode"].startswith("Synthetic network from open data")


def test_non_canadian_aoi_flagged(tmp_path):
    j = _preview(tmp_path, TOKYO)
    assert j["city"] is None
    assert j["in_canada"] is False


def test_existing_preview_fields_unchanged(tmp_path):
    j = _preview(tmp_path, OTTAWA)
    assert j["geometry"]["type"] == "Polygon"
    assert len(j["bbox"]) == 4
    assert j["area_km2"] > 0
