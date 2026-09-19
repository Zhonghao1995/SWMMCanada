"""Fetch-layer tests for the Waterloo adapter: layer routing and where-clauses — all
against a fake client (no network)."""
from swmmcanada.sources.cities.waterloo import (
    fetch_waterloo_land,
    fetch_waterloo_sanitary,
    fetch_waterloo_storm,
)

BBOX = (-80.5270, 43.4625, -80.5190, 43.4680)


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def _params(client, fragment):
    return next(p for u, p in client.calls if fragment in u)


def test_storm_hits_collectors_manholes_outlets():
    client = FakeClient()
    out = fetch_waterloo_storm(BBOX, client=client)
    # Leads, culverts, ditches and site drains share the layer; only collectors are mains.
    assert _params(client, "Stormwater_Gravity_Mains/FeatureServer/0/query")["where"] == \
        "ASSET_TYPE = 'Collector'"
    assert _params(client, "Stormwater_Manholes/FeatureServer/0/query")["where"] == "1=1"
    assert _params(client, "Stormwater_Outlets/FeatureServer/0/query")["where"] == "1=1"
    assert set(out) == {"mains", "manholes", "outfalls"}
    assert all("ZpeBVw5o1kjit7LT" in u for u, _ in client.calls)   # the City of Waterloo org


def test_sanitary_hits_the_gravity_mains_layer():
    client = FakeClient()
    out = fetch_waterloo_sanitary(BBOX, client=client)
    assert [u for u, _ in client.calls] == [
        "https://services.arcgis.com/ZpeBVw5o1kjit7LT/arcgis/rest/services/"
        "Sanitary_Sewer_Gravity_Mains/FeatureServer/0/query"]
    assert set(out) == {"mains"}


def test_land_hits_catchbasins_lots_buildings_and_leaves_road_parcels_out():
    client = FakeClient()
    out = fetch_waterloo_land(BBOX, client=client)
    assert _params(client, "Stormwater_Catch_Basins/FeatureServer/0/query")
    assert _params(client, "Buildings/FeatureServer/0/query")
    where = _params(client, "Property_Fabric/FeatureServer/1358/query")["where"]
    assert "'ROAD PARCEL'" in where and "'RAILWAY PARCEL'" in where and "IS NULL" in where
    assert set(out) == {"catchbasins", "parcels", "buildings"}


def test_accepts_aoi_object_with_bbox_attr():
    class Aoi:
        bbox = BBOX

    client = FakeClient()
    fetch_waterloo_storm(Aoi(), client=client)
    assert client.calls[0][1]["geometry"] == ",".join(map(str, BBOX))
