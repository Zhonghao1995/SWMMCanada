"""GET /api/v1/coverage: registry-backed discovery for integrating clients.

aiswmm's "supported cities" hint once drifted (Regina missing for a week);
this endpoint makes the registry the queryable source of truth, so client
lists can never rot again.
"""
from fastapi.testclient import TestClient

from swmmcanada.api import create_app
from swmmcanada.sources.cities.registry import CITIES


def _coverage(tmp_path):
    client = TestClient(create_app(pipeline=lambda *a, **k: None, workdir=tmp_path, run_inline=True))
    r = client.get("/api/v1/coverage")
    assert r.status_code == 200
    return r.json()


def test_every_registered_city_is_listed(tmp_path):
    j = _coverage(tmp_path)
    keys = [c["key"] for c in j["real_network_cities"]]
    assert keys == [spec.key for spec in CITIES]
    assert len(keys) == len(set(keys))


def test_city_entries_carry_label_bbox_sanitary(tmp_path):
    j = _coverage(tmp_path)
    by_key = {c["key"]: c for c in j["real_network_cities"]}
    regina = by_key["regina"]
    assert regina["has_sanitary"] is True
    assert "Regina" in regina["label"]
    assert len(regina["coverage_bbox"]) == 4
    victoria = by_key["victoria"]
    assert "Victoria" in victoria["label"]


def test_synthesis_fallback_is_stated(tmp_path):
    j = _coverage(tmp_path)
    assert "Canada" in j["synthesis"]
