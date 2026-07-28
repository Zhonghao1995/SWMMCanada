"""Fetch-layer tests for the Coquitlam adapter: layer routing, where-clauses and the
URL-encoded service name — all against a fake client (no network)."""
from swmmcanada.sources.cities.coquitlam import (
    fetch_coquitlam_land,
    fetch_coquitlam_sanitary,
    fetch_coquitlam_storm,
)

BBOX = (-122.800, 49.272, -122.788, 49.281)


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def _urls(client):
    return [u for u, _ in client.calls]


def test_storm_hits_mains_manholes_outfalls_with_operating_filter():
    client = FakeClient()
    out = fetch_coquitlam_storm(BBOX, client=client)
    urls = _urls(client)
    assert any("Drainage%20Utility/FeatureServer/16/query" in u for u in urls)   # mains
    assert any("Drainage%20Utility/FeatureServer/6/query" in u for u in urls)    # manholes
    assert any("Drainage%20Utility/FeatureServer/10/query" in u for u in urls)   # outfalls
    mains_params = next(p for u, p in client.calls if "/16/query" in u)
    assert mains_params["where"] == "STATUS='OPERATING'"
    assert set(out) == {"mains", "manholes", "outfalls"}


def test_sanitary_hits_sanitary_service_with_operating_filter():
    client = FakeClient()
    out = fetch_coquitlam_sanitary(BBOX, client=client)
    urls = _urls(client)
    assert any("Sanitary%20Utility/FeatureServer/10/query" in u for u in urls)   # mains
    assert any("Sanitary%20Utility/FeatureServer/0/query" in u for u in urls)    # manholes
    mains_params = next(p for u, p in client.calls if "/10/query" in u)
    assert mains_params["where"] == "STATUS='OPERATING'"
    assert set(out) == {"mains", "manholes"}


def test_land_hits_catchbasins_parcels_buildings():
    client = FakeClient()
    out = fetch_coquitlam_land(BBOX, client=client)
    urls = _urls(client)
    assert any("Drainage%20Utility/FeatureServer/11/query" in u for u in urls)   # catchbasins
    assert any("Cadastral/FeatureServer/13/query" in u for u in urls)            # parcels
    assert any("Cadastral/FeatureServer/15/query" in u for u in urls)            # buildings
    assert set(out) == {"catchbasins", "parcels", "buildings"}


def test_accepts_aoi_object_with_bbox_attr():
    class Aoi:
        bbox = BBOX

    client = FakeClient()
    fetch_coquitlam_storm(Aoi(), client=client)
    geom = client.calls[0][1]["geometry"]
    assert geom == ",".join(map(str, BBOX))
