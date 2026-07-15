"""Offline replay of City-of-Surrey ArcGIS responses through an injected fake client.

Fetch contract under test (audit 2026-07-14, explicit-topology upgrade):
  1. Mains from Public/Drainage layer 14, filtered MAIN_TYPE2='Gravity' AND
     STATUS='In Service', bbox envelope, paginated. (The OpenData view strips the
     UP_NODE/DOWN_NODE columns; layer 14 carries them.)
  2. Nodes = Public/Drainage manholes(4) + catch basins(2) + devices(3), all with NODE_NO;
     outfalls are the 'Outlet'-classified devices selected client-side from the node pull.
  3. Land: catch basins (24) + parcels (148) + buildings (155) by bbox (OpenData).
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
    LAND_PARCELS,
    PUB_CATCHBASINS,
    PUB_DEVICES,
    PUB_MAINS,
    PUB_MANHOLES,
    SAN_MAINS,
    fetch_surrey_land,
    fetch_surrey_sanitary,
    fetch_surrey_storm,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "surrey"
BBOX = (-122.825, 49.118, -122.821, 49.122)


def _load(name):
    return json.loads((FIX / f"{name}.geojson").read_text())["features"]


PIPES = _load("pub_mains")
NODES = _load("pub_nodes")
CATCHBASIN_FEATS = _load("catchbasins")
SAN_PIPES = _load("sanitary_mains")
MANHOLE_NODES = [f for f in NODES if "MANHOLE_TYPE2" in (f.get("properties") or {})]
DEVICE_NODES = [f for f in NODES
                if (f.get("properties") or {}).get("DEVICE_CLASSIFICATION") is not None]


def _layer_id(url):
    m = re.search(r"/(\d+)/query/?$", url)
    return int(m.group(1)) if m else None


def _is_pub(url):
    return "/Public/Drainage/" in url


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
        self.calls.append((layer, params, url))

        if _is_pub(url) and layer == PUB_MAINS:
            feats = PIPES
            if "MAIN_TYPE2" in where:
                want = re.search(r"MAIN_TYPE2\s*=\s*'([^']+)'", where).group(1)
                feats = [f for f in feats if f["properties"].get("MAIN_TYPE2") == want]
            if "STATUS" in where:
                want = re.search(r"STATUS\s*=\s*'([^']+)'", where).group(1)
                feats = [f for f in feats if f["properties"].get("STATUS") == want]
            return self._page(feats, params)
        if _is_pub(url) and layer == PUB_MANHOLES:
            return self._page(MANHOLE_NODES, params)
        if _is_pub(url) and layer == PUB_CATCHBASINS:
            return self._page([], params)
        if _is_pub(url) and layer == PUB_DEVICES:
            return self._page(DEVICE_NODES, params)
        if layer == CATCHBASINS:
            return self._page(CATCHBASIN_FEATS, params)
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
    assert set(res) == {"pipes", "nodes", "outfalls"}
    for f in res["pipes"]:
        assert f["type"] == "Feature" and "properties" in f and "geometry" in f


def test_storm_nodes_carry_node_no_and_rim():
    res = fetch_surrey_storm(BBOX, client=FakeClient())
    assert res["nodes"], "expected drainage nodes from the fixture"
    assert any((f["properties"] or {}).get("NODE_NO") for f in res["nodes"])
    assert any("RIM_ELEVATION" in (f["properties"] or {}) for f in res["nodes"])


def test_mains_filtered_to_in_service_gravity():
    client = FakeClient()
    res = fetch_surrey_storm(BBOX, client=client)
    assert res["pipes"], "expected gravity mains from the fixture"
    assert all(f["properties"]["MAIN_TYPE2"] == "Gravity" for f in res["pipes"])
    assert all(f["properties"]["STATUS"] == "In Service" for f in res["pipes"])
    mains_calls = [p for (layer, p, url) in client.calls if layer == PUB_MAINS and _is_pub(url)]
    assert mains_calls, "Public/Drainage mains layer was never queried"
    assert any("Gravity" in c.get("where", "") and "In Service" in c.get("where", "")
               for c in mains_calls)


def test_outfalls_are_outlet_classified_devices():
    res = fetch_surrey_storm(BBOX, client=FakeClient())
    assert res["outfalls"], "expected Outlet devices from the fixture"
    assert all(f["properties"]["DEVICE_CLASSIFICATION"] == "Outlet" for f in res["outfalls"])
    node_set = {id(f) for f in res["nodes"]}
    assert all(id(f) in node_set for f in res["outfalls"])   # selected from the node pull


def test_mains_request_uses_envelope_geometry_filter():
    client = FakeClient()
    fetch_surrey_storm(BBOX, client=client)
    first = next(p for (layer, p, url) in client.calls if layer == PUB_MAINS and _is_pub(url))
    assert first.get("geometryType") == "esriGeometryEnvelope"
    assert first.get("spatialRel") == "esriSpatialRelIntersects"
    assert first.get("geometry") == "-122.825,49.118,-122.821,49.122"
    assert str(first.get("inSR")) == "4326"
    assert first.get("f") == "geojson"


def test_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_surrey_storm(AOI(), client=FakeClient())
    assert res["pipes"] and res["nodes"]


# --- sanitary fetch (second tagged system, ADR 0011) -----------------------------

def test_sanitary_returns_in_service_gravity_mains():
    """San Mains (41) must be queried with the Gravity + In Service where-clause: the layer
    also carries Force/Stub and Abandoned/Proposed lines that are not part of the skeleton."""
    client = FakeClient()
    res = fetch_surrey_sanitary(BBOX, client=client)
    assert set(res) == {"pipes"}
    assert res["pipes"], "expected in-service gravity san mains from the fixture"
    assert all(f["properties"]["MAIN_TYPE2"] == "Gravity" for f in res["pipes"])
    san_calls = [p for (layer, p, url) in client.calls if layer == SAN_MAINS]
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
    layers = {layer for (layer, _, url) in client.calls if not _is_pub(url)}
    assert CATCHBASINS in layers and LAND_PARCELS in layers and BUILDINGS in layers


# --- pagination ----------------------------------------------------------------

class PagingClient:
    """First mains page reports exceededTransferLimit=True; concatenation must continue."""

    PAGE = 60  # fixture has 142 gravity mains -> three pages

    def __init__(self):
        self.mains_pages_served = 0
        self.calls = []

    def get_json(self, url, params):
        layer = _layer_id(url)
        self.calls.append((layer, params))
        if _is_pub(url) and layer == PUB_MAINS:
            offset = int(params.get("resultOffset", 0) or 0)
            page = PIPES[offset: offset + self.PAGE]
            exceeded = (offset + self.PAGE) < len(PIPES)
            self.mains_pages_served += 1
            return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}
        return {"type": "FeatureCollection", "features": []}


def test_pagination_concatenates_all_pages():
    client = PagingClient()
    res = fetch_surrey_storm(BBOX, client=client)
    assert client.mains_pages_served >= 2, "expected the fetcher to request a second page"
    assert len(res["pipes"]) == len(PIPES)


def test_pagination_offsets_advance():
    client = PagingClient()
    fetch_surrey_storm(BBOX, client=client)
    offsets = [int(p.get("resultOffset", 0) or 0) for (layer, p) in client.calls if layer == PUB_MAINS]
    assert offsets[0] == 0
    assert max(offsets) > 0, "later mains pages must use a non-zero resultOffset"


# --- esri-json -> geojson fallback --------------------------------------------

class EsriJsonClient:
    """Returns Esri JSON (attributes + paths/x,y) instead of GeoJSON, to prove the adapter's
    esri_to_geojson fallback fires when a layer doesn't honour f=geojson."""

    def get_json(self, url, params):
        layer = _layer_id(url)
        if _is_pub(url) and layer == PUB_MAINS:
            return {"features": [{
                "attributes": {"OBJECTID": 7, "FACILITYID": "DM7", "MAIN_TYPE2": "Gravity",
                               "STATUS": "In Service", "UP_NODE": "N1", "DOWN_NODE": "N2",
                               "MAIN_SIZE": 300, "MATERIAL": "PVC", "MAIN_SHAPE": "Circular",
                               "UP_ELEVATION": 12.0, "DOWN_ELEVATION": 10.0, "SHAPE.LEN": 40.0},
                "geometry": {"paths": [[[-122.82, 49.12], [-122.821, 49.121]]]},
            }]}
        if _is_pub(url) and layer == PUB_DEVICES:
            return {"features": [{
                "attributes": {"OBJECTID": 9, "NODE_NO": "N2",
                               "DEVICE_CLASSIFICATION": "Outlet", "RIM_ELEVATION": 13.0},
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
    """The esri->geojson path must produce features build_surrey_network can consume — and
    with node ids resolving, the explicit-topology path names nodes by NODE_NO."""
    from swmmcanada.sources.cities.surrey import build_surrey_network

    res = build_surrey_network(fetch_surrey_storm(BBOX, client=EsriJsonClient()))
    assert len(res.network.conduits) > 0
    assert res.diagnostics["topology"] == "explicit_node_ids"
