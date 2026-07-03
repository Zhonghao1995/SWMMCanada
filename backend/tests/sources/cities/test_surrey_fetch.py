"""Offline replay of City-of-Surrey ArcGIS responses through an injected fake client.

Fetch contract under test (see tests/fixtures/surrey/README.md):
  1. Mains (layer 18) filtered to MAIN_TYPE2='Gravity', bbox envelope, paginated.
  2. Outfalls (layer 25) filtered to DEVICE_CLASSIFICATION='Outlet'.
  3. Land: catch basins (24) + parcels (148) + buildings (155) by bbox.
  4. Surrey serves real GeoJSON under f=geojson, but the adapter still converts any feature
     that comes back as Esri JSON (attributes/paths) — the esri->geojson fallback path.
"""
import json
import re
from pathlib import Path

from swmmcanada.sources.cities.surrey import (
    _SAN_WHERE,
    BUILDINGS,
    CATCHBASINS,
    DRAINAGE_DEVICES,
    LAND_PARCELS,
    MANHOLES,
    SAN_MAINS,
    STORM_MAINS,
    fetch_surrey_land,
    fetch_surrey_sanitary,
    fetch_surrey_storm,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "surrey"
BBOX = (-122.825, 49.118, -122.821, 49.122)


def _load(name):
    return json.loads((FIX / f"{name}.geojson").read_text())["features"]


PIPES = _load("storm_pipes")
OUTFALLS = _load("outfalls")
CATCHBASIN_FEATS = _load("catchbasins")
MANHOLE_FEATS = _load("manholes")
SAN_PIPES = _load("sanitary_mains")


def _layer_id(url):
    m = re.search(r"/(\d+)/query/?$", url)
    return int(m.group(1)) if m else None


class FakeClient:
    """Replays fixtures as f=geojson responses, honouring where-filters, offset and count.

    Records every (layer_id, params) so tests can assert request shape.
    """

    def __init__(self):
        self.calls = []

    def _page(self, feats, params, exceed_when_full=True):
        offset = int(params.get("resultOffset", 0) or 0)
        count = params.get("resultRecordCount")
        page = feats[offset:] if count is None else feats[offset: offset + int(count)]
        exceeded = exceed_when_full and count is not None and (offset + int(count)) < len(feats)
        return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}

    def get_json(self, url, params):
        layer = _layer_id(url)
        where = params.get("where", "")
        self.calls.append((layer, params))

        if layer == STORM_MAINS:
            feats = PIPES
            if "MAIN_TYPE2" in where:
                want = re.search(r"MAIN_TYPE2\s*=\s*'([^']+)'", where).group(1)
                feats = [f for f in feats if f["properties"].get("MAIN_TYPE2") == want]
            return self._page(feats, params)
        if layer == DRAINAGE_DEVICES:
            feats = OUTFALLS
            if "DEVICE_CLASSIFICATION" in where:
                want = re.search(r"DEVICE_CLASSIFICATION\s*=\s*'([^']+)'", where).group(1)
                feats = [f for f in feats if f["properties"].get("DEVICE_CLASSIFICATION") == want]
            return self._page(feats, params)
        if layer == CATCHBASINS:
            return self._page(CATCHBASIN_FEATS, params)
        if layer == MANHOLES:
            return self._page(MANHOLE_FEATS, params)
        if layer == SAN_MAINS:
            feats = SAN_PIPES
            if "MAIN_TYPE2" in where:
                want = re.search(r"MAIN_TYPE2\s*=\s*'([^']+)'", where).group(1)
                feats = [f for f in feats if f["properties"].get("MAIN_TYPE2") == want]
            if "STATUS" in where:
                want = re.search(r"STATUS\s*=\s*'([^']+)'", where).group(1)
                feats = [f for f in feats if f["properties"].get("STATUS") == want]
            return self._page(feats, params)
        if layer in (LAND_PARCELS, BUILDINGS):
            return {"type": "FeatureCollection", "features": []}
        return {"type": "FeatureCollection", "features": []}


# --- storm fetch ---------------------------------------------------------------

def test_storm_returns_three_keys_as_geojson_features():
    res = fetch_surrey_storm(BBOX, client=FakeClient())
    assert set(res) == {"pipes", "outfalls", "manholes"}   # manholes -> rim depths
    for f in res["pipes"]:
        assert f["type"] == "Feature" and "properties" in f and "geometry" in f


def test_storm_manholes_carry_rim_elevation():
    res = fetch_surrey_storm(BBOX, client=FakeClient())
    assert res["manholes"], "expected drainage manholes from the fixture"
    assert all(f["geometry"]["type"] == "Point" for f in res["manholes"])
    assert any("RIM_ELEVATION" in f["properties"] for f in res["manholes"])


def test_mains_filtered_to_gravity():
    client = FakeClient()
    res = fetch_surrey_storm(BBOX, client=client)
    assert res["pipes"], "expected gravity mains from the fixture"
    assert all(f["properties"]["MAIN_TYPE2"] == "Gravity" for f in res["pipes"])
    mains_calls = [p for (layer, p) in client.calls if layer == STORM_MAINS]
    assert mains_calls, "mains layer was never queried"
    assert any("MAIN_TYPE2" in c.get("where", "") and "Gravity" in c.get("where", "")
               for c in mains_calls)


def test_outfalls_filtered_to_outlet_classification():
    client = FakeClient()
    res = fetch_surrey_storm(BBOX, client=client)
    assert res["outfalls"], "expected Outlet devices from the fixture"
    assert all(f["properties"]["DEVICE_CLASSIFICATION"] == "Outlet" for f in res["outfalls"])
    dev_calls = [p for (layer, p) in client.calls if layer == DRAINAGE_DEVICES]
    assert dev_calls, "drainage-devices layer was never queried"
    assert any("DEVICE_CLASSIFICATION" in c.get("where", "") and "Outlet" in c.get("where", "")
               for c in dev_calls)


def test_mains_request_uses_envelope_geometry_filter():
    client = FakeClient()
    fetch_surrey_storm(BBOX, client=client)
    first = next(p for (layer, p) in client.calls if layer == STORM_MAINS)
    assert first.get("geometryType") == "esriGeometryEnvelope"
    assert first.get("spatialRel") == "esriSpatialRelIntersects"
    assert first.get("geometry") == "-122.825,49.118,-122.821,49.122"
    assert str(first.get("inSR")) == "4326"
    assert first.get("f") == "geojson"


def test_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_surrey_storm(AOI(), client=FakeClient())
    assert res["pipes"] and res["outfalls"]


# --- sanitary fetch (second tagged system, ADR 0011) -----------------------------

def test_sanitary_returns_in_service_gravity_mains():
    """San Mains (41) must be queried with the Gravity + In Service where-clause: the layer
    also carries Force/Stub and Abandoned/Proposed lines that are not part of the skeleton."""
    client = FakeClient()
    res = fetch_surrey_sanitary(BBOX, client=client)
    assert set(res) == {"pipes"}
    assert res["pipes"], "expected in-service gravity san mains from the fixture"
    assert all(f["properties"]["MAIN_TYPE2"] == "Gravity" for f in res["pipes"])
    san_calls = [p for (layer, p) in client.calls if layer == SAN_MAINS]
    assert san_calls, "san mains layer was never queried"
    assert all(p.get("where", "") == _SAN_WHERE for p in san_calls)
    assert "Gravity" in _SAN_WHERE and "In Service" in _SAN_WHERE


# --- land fetch ----------------------------------------------------------------

def test_land_returns_three_keys():
    res = fetch_surrey_land(BBOX, client=FakeClient())
    assert set(res) == {"catchbasins", "parcels", "buildings"}
    assert res["catchbasins"], "expected catch basins from the fixture"
    # parcels + buildings layers must actually be requested even though the fake returns none
    client = FakeClient()
    fetch_surrey_land(BBOX, client=client)
    layers = {layer for (layer, _) in client.calls}
    assert CATCHBASINS in layers and LAND_PARCELS in layers and BUILDINGS in layers


# --- pagination ----------------------------------------------------------------

class PagingClient:
    """First mains page reports exceededTransferLimit=True; concatenation must continue."""

    PAGE = 20  # fixture has 35 gravity mains -> page1=20 (exceeded), page2=15 (done)

    def __init__(self):
        self.mains_pages_served = 0
        self.calls = []

    def get_json(self, url, params):
        layer = _layer_id(url)
        self.calls.append((layer, params))
        if layer == STORM_MAINS:
            offset = int(params.get("resultOffset", 0) or 0)
            page = PIPES[offset: offset + self.PAGE]
            exceeded = (offset + self.PAGE) < len(PIPES)
            self.mains_pages_served += 1
            return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}
        if layer == DRAINAGE_DEVICES:
            return {"type": "FeatureCollection", "features": OUTFALLS}
        return {"type": "FeatureCollection", "features": []}


def test_pagination_concatenates_all_pages():
    client = PagingClient()
    res = fetch_surrey_storm(BBOX, client=client)
    assert client.mains_pages_served >= 2, "expected the fetcher to request a second page"
    assert len(res["pipes"]) == len(PIPES)  # all 35 concatenated


def test_pagination_offsets_advance():
    client = PagingClient()
    fetch_surrey_storm(BBOX, client=client)
    offsets = [int(p.get("resultOffset", 0) or 0) for (layer, p) in client.calls if layer == STORM_MAINS]
    assert offsets[0] == 0
    assert max(offsets) > 0, "later mains pages must use a non-zero resultOffset"


# --- esri-json -> geojson fallback --------------------------------------------

class EsriJsonClient:
    """Returns Esri JSON (attributes + paths/x,y) instead of GeoJSON, to prove the adapter's
    esri_to_geojson fallback fires when a layer doesn't honour f=geojson."""

    def get_json(self, url, params):
        layer = _layer_id(url)
        if layer == STORM_MAINS:
            return {"features": [{
                "attributes": {"OBJECTID": 7, "FACILITYID": "DM7", "MAIN_TYPE2": "Gravity",
                               "MAIN_SIZE": 300, "MATERIAL": "PVC", "MAIN_SHAPE": "Circular",
                               "UP_ELEVATION": 12.0, "DOWN_ELEVATION": 10.0, "SHAPE.LEN": 40.0},
                "geometry": {"paths": [[[-122.82, 49.12], [-122.821, 49.121]]]},
            }]}
        if layer == DRAINAGE_DEVICES:
            return {"features": [{
                "attributes": {"OBJECTID": 9, "DEVICE_CLASSIFICATION": "Outlet"},
                "geometry": {"x": -122.821, "y": 49.121},
            }]}
        return {"features": []}


def test_esri_json_features_are_converted_to_geojson():
    res = fetch_surrey_storm(BBOX, client=EsriJsonClient())
    pipe = res["pipes"][0]
    assert pipe["type"] == "Feature"
    assert "properties" in pipe and pipe["properties"]["FACILITYID"] == "DM7"
    assert pipe["geometry"]["type"] == "LineString"
    assert pipe["geometry"]["coordinates"][0] == [-122.82, 49.12]

    outfall = res["outfalls"][0]
    assert outfall["geometry"]["type"] == "Point"
    assert outfall["properties"]["DEVICE_CLASSIFICATION"] == "Outlet"


def test_converted_esri_network_still_builds():
    """The esri->geojson path must produce features build_surrey_network can consume."""
    from swmmcanada.sources.cities.surrey import build_surrey_network

    res = build_surrey_network(fetch_surrey_storm(BBOX, client=EsriJsonClient()))
    assert len(res.network.conduits) > 0
