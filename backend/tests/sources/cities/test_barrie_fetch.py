"""Fetch-layer tests for the Barrie adapter: layer routing, type/status where-clauses and
the device-derived catchbasin filter — all against a fake client (no network)."""
from swmmcanada.sources.cities.barrie import (
    fetch_barrie_land,
    fetch_barrie_sanitary,
    fetch_barrie_storm,
)

BBOX = (-79.700, 44.385, -79.688, 44.394)


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        if "StormInfrastructure/MapServer/0" in url:
            return {"features": [
                {"type": "Feature", "properties": {"ASSETID": "1", "TYPE": "CATCH BASIN"},
                 "geometry": {"type": "Point", "coordinates": [-79.69, 44.39]}},
                {"type": "Feature", "properties": {"ASSETID": "2", "TYPE": "MAINTENANCE HOLE"},
                 "geometry": {"type": "Point", "coordinates": [-79.691, 44.39]}},
            ]}
        return {"features": []}


def _params(client, fragment):
    return next(p for u, p in client.calls if fragment in u)


def test_storm_filters_piped_active_types():
    client = FakeClient()
    out = fetch_barrie_storm(BBOX, client=client)
    where = _params(client, "StormInfrastructure/MapServer/1")["where"]
    assert "LOCAL" in where and "TRUNK" in where and "STATUS='ACTIVE'" in where
    assert "WATERCOURSE" not in where and "DITCH" not in where
    assert set(out) == {"mains", "devices"}


def test_sanitary_excludes_force_mains():
    client = FakeClient()
    out = fetch_barrie_sanitary(BBOX, client=client)
    where = _params(client, "SanitaryInfrastructure/MapServer/2")["where"]
    assert "'LOCAL'" in where and "'TRUNK'" in where and "FORCE" not in where
    assert set(out) == {"mains", "devices"}


def test_land_filters_catchbasin_family_from_devices():
    client = FakeClient()
    out = fetch_barrie_land(BBOX, client=client)
    assert any("ParcelPublishing/MapServer/2" in u for u, _ in client.calls)
    assert any("FacilitiesStreets/MapServer/36" in u for u, _ in client.calls)
    assert len(out["catchbasins"]) == 1                       # CB kept, MH filtered out
    assert out["catchbasins"][0]["properties"]["TYPE"] == "CATCH BASIN"


def test_accepts_aoi_object_with_bbox_attr():
    class Aoi:
        bbox = BBOX

    client = FakeClient()
    fetch_barrie_storm(Aoi(), client=client)
    assert client.calls[0][1]["geometry"] == ",".join(map(str, BBOX))
