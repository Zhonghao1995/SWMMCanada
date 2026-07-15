"""Fetch-layer tests for the Vancouver adapter: where-clauses, by-id manhole resolution and
the Opendatasoft in_bbox argument order — all against a fake client (no network)."""
from swmmcanada.sources.cities.vancouver import (
    fetch_vancouver_land,
    fetch_vancouver_sanitary,
    fetch_vancouver_storm,
)

BBOX = (-123.125, 49.275, -123.117, 49.281)


class FakeClient:
    """Returns one main referencing two manholes, then empty pages; records every call."""

    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        if "swGravityMain" in url:
            return {"features": [{
                "type": "Feature",
                "properties": {"facilityid": "P1", "frommh": "MH1", "tomh": "MH2",
                               "diameter": 300, "eflnttype": "Storm"},
                "geometry": {"type": "LineString",
                             "coordinates": [[-123.12, 49.276], [-123.121, 49.277]]},
            }]}
        if "swManhole" in url:
            return {"features": [{
                "type": "Feature",
                "properties": {"facilityid": "MH1", "rimelev": 30.0},
                "geometry": {"type": "Point", "coordinates": [-123.12, 49.276]},
            }]}
        return {"features": []}


def _params_for(calls, fragment):
    return [p for u, p in calls if fragment in u]


def test_storm_where_includes_combined_and_in_service():
    client = FakeClient()
    out = fetch_vancouver_storm(BBOX, client=client)
    where = _params_for(client.calls, "swGravityMain")[0]["where"]
    assert "Combined" in where and "Storm" in where and "In Service" in where
    assert len(out["mains"]) == 1


def test_sanitary_where_excludes_combined():
    client = FakeClient()
    fetch_vancouver_sanitary(BBOX, client=client)
    where = _params_for(client.calls, "swGravityMain")[0]["where"]
    assert "Sanitary" in where and "Combined" not in where


def test_manholes_fetched_by_quoted_facilityid():
    client = FakeClient()
    out = fetch_vancouver_storm(BBOX, client=client)
    mh_where = _params_for(client.calls, "swManhole")[0]["where"]
    assert "facilityid IN ('MH1','MH2')" == mh_where
    assert len(out["manholes"]) == 1


def test_opendata_in_bbox_is_lat_lon_ordered():
    """Opendatasoft's in_bbox takes (lat_min, lon_min, lat_max, lon_max) — locked here
    because getting it backwards silently returns zero features."""
    client = FakeClient()
    out = fetch_vancouver_land(BBOX, client=client)
    assert set(out) == {"catchbasins", "parcels", "buildings"}
    wheres = [p["where"] for u, p in client.calls if "opendata" in u]
    assert len(wheres) == 3
    for w in wheres:
        assert w == "in_bbox(geom, 49.275, -123.125, 49.281, -123.117)"
