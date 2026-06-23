"""Offline replay of recorded City-of-London ArcGIS responses through an injected fake
client (mirrors the Victoria fetch tests / test_climate.py's pattern).

Fetch contract under test (see tests/fixtures/london/README.md):
  1. PIPES (layer 5) filtered to FlowType='STM', bbox envelope, paginated.
  2. Endpoint ids harvested from Upstream/DownstreamID (no prefix split — a node can be in
     any node layer).
  3. Nodes fetched BY GIS_FeatureKey (where=GIS_FeatureKey IN (...)) from manholes(2),
     other_nodes(3) and outfalls(4), chunked — NOT by bbox.
"""
import json
import re
from pathlib import Path

import pytest

from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.london import (
    MANHOLES,
    OTHER_NODES,
    OUTFALLS,
    PIPES,
    fetch_london_storm,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "london"

# A bbox loosely covering the captured downtown AOI (EPSG:4326 lon/lat). The fake client
# ignores the actual envelope numbers — geometry filtering is the server's job in
# production; here we only assert the *request shape*.
BBOX = (-81.260, 42.980, -81.254, 42.985)


def _load(name):
    return json.loads((FIX / f"{name}.geojson").read_text())["features"]


MAINS_FEATURES = _load("mains")
NODE_FIXTURES = {
    MANHOLES: _load("manholes"),
    OTHER_NODES: _load("other_nodes"),
    OUTFALLS: _load("outfalls"),
}


def _layer_id(url):
    """Trailing '/<id>/query' segment of an ArcGIS layer query URL."""
    m = re.search(r"/(\d+)/query/?$", url)
    return int(m.group(1)) if m else None


def _parse_in_ids(where):
    """Pull the quoted ids out of a `GIS_FeatureKey IN ('a','b',...)` clause."""
    return re.findall(r"'([^']+)'", where.split("IN", 1)[1])


class FakeClient:
    """Replays the *.geojson fixtures as if they were live f=geojson responses.

    Records every (layer_id, params) it is asked for so tests can assert the request
    shape: STM filter on pipes, GIS_FeatureKey-IN (not bbox) on every node layer.
    """

    def __init__(self):
        self.calls = []  # list of (layer_id, params)

    def get_json(self, url, params):
        layer = _layer_id(url)
        where = params.get("where", "")
        self.calls.append((layer, params))

        if layer == PIPES:
            feats = MAINS_FEATURES
            if "FlowType" in where:
                want = re.search(r"FlowType\s*=\s*'([^']+)'", where).group(1)
                feats = [f for f in feats if f["properties"].get("FlowType") == want]
            offset = int(params.get("resultOffset", 0) or 0)
            count = params.get("resultRecordCount")
            page = feats[offset:] if count is None else feats[offset : offset + int(count)]
            exceeded = count is not None and (offset + int(count)) < len(feats)
            return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}

        if layer in NODE_FIXTURES:
            assert "GIS_FeatureKey IN" in where, (
                f"node layer {layer} must be queried by GIS_FeatureKey, got: {where!r}")
            wanted = set(_parse_in_ids(where))
            feats = [f for f in NODE_FIXTURES[layer]
                     if f["properties"]["GIS_FeatureKey"] in wanted]
            return {"type": "FeatureCollection", "features": feats}

        return {"type": "FeatureCollection", "features": []}


# --- helpers to compute the expected join from the fixtures -------------------

def _referenced_ids():
    ids = set()
    for f in MAINS_FEATURES:
        for key in ("UpstreamID", "DownstreamID"):
            v = f["properties"].get(key)
            if v:
                ids.add(v)
    return ids


def _fixture_keys(layer):
    return {f["properties"]["GIS_FeatureKey"] for f in NODE_FIXTURES[layer]}


# --- tests -------------------------------------------------------------------

def test_returns_four_keys_as_geojson_features():
    res = fetch_london_storm(BBOX, client=FakeClient())
    assert set(res) == {"mains", "manholes", "other_nodes", "outfalls"}
    for key in res:
        assert isinstance(res[key], list)
    for f in res["mains"]:
        assert f["type"] == "Feature"
        assert "properties" in f and "geometry" in f


def test_mains_filtered_to_stm():
    client = FakeClient()
    res = fetch_london_storm(BBOX, client=client)
    assert res["mains"], "expected STM mains from the fixture"
    assert all(f["properties"]["FlowType"] == "STM" for f in res["mains"])
    mains_calls = [p for (layer, p) in client.calls if layer == PIPES]
    assert mains_calls, "pipes layer was never queried"
    assert any("FlowType" in c.get("where", "") and "STM" in c.get("where", "") for c in mains_calls)


def test_mains_request_uses_envelope_geometry_filter():
    client = FakeClient()
    fetch_london_storm(BBOX, client=client)
    first = next(p for (layer, p) in client.calls if layer == PIPES)
    assert first.get("geometryType") == "esriGeometryEnvelope"
    assert first.get("spatialRel") == "esriSpatialRelIntersects"
    assert first.get("geometry") == "-81.26,42.98,-81.254,42.985"
    assert str(first.get("inSR")) == "4326"
    assert first.get("f") == "geojson"


def test_nodes_fetched_by_featurekey_not_bbox():
    client = FakeClient()
    fetch_london_storm(BBOX, client=client)
    node_calls = [(layer, p) for (layer, p) in client.calls if layer in NODE_FIXTURES]
    assert node_calls, "no node layers were queried"
    for layer, params in node_calls:
        where = params.get("where", "")
        assert "GIS_FeatureKey IN" in where, f"layer {layer} not queried by key: {where!r}"
        assert "geometry" not in params or not params.get("geometry")
        assert params.get("geometryType") in (None, "")


def test_all_three_node_layers_are_queried():
    """A referenced node can live in manholes / other-nodes / outfalls; all three layers
    must be fetched by id (Victoria split by prefix; London cannot, so it queries all)."""
    client = FakeClient()
    fetch_london_storm(BBOX, client=client)
    queried = {layer for (layer, _) in client.calls if layer in NODE_FIXTURES}
    assert queried == {MANHOLES, OTHER_NODES, OUTFALLS}


def test_returned_nodes_cover_referenced_ids_present_in_fixtures():
    res = fetch_london_storm(BBOX, client=FakeClient())
    referenced = _referenced_ids()
    for layer, key in ((MANHOLES, "manholes"), (OTHER_NODES, "other_nodes"), (OUTFALLS, "outfalls")):
        present = referenced & _fixture_keys(layer)
        got = {f["properties"]["GIS_FeatureKey"] for f in res[key]}
        assert got == present, f"{key}: expected {len(present)} nodes, got {len(got)}"
        for f in res[key]:
            assert f["type"] == "Feature" and f["geometry"]["type"] == "Point"


def test_nodes_are_deduped_by_featurekey():
    res = fetch_london_storm(BBOX, client=FakeClient())
    for key in ("manholes", "other_nodes", "outfalls"):
        ids = [f["properties"]["GIS_FeatureKey"] for f in res[key]]
        assert len(ids) == len(set(ids)), f"{key} contains duplicate GIS_FeatureKeys"


def test_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_london_storm(AOI(), client=FakeClient())
    assert res["mains"] and res["manholes"]


# --- pagination --------------------------------------------------------------

class PagingClient:
    """First pipes page reports exceededTransferLimit=True, second page finishes.

    Splits the fixture pipes into pages regardless of the requested resultRecordCount,
    to prove the fetcher concatenates pages until the limit is no longer exceeded.
    """

    PAGE = 25  # fixture has 44 pipes -> page1=25 (exceeded), page2=19 (done)

    def __init__(self):
        self.mains_pages_served = 0
        self.calls = []

    def get_json(self, url, params):
        layer = _layer_id(url)
        self.calls.append((layer, params))
        if layer == PIPES:
            offset = int(params.get("resultOffset", 0) or 0)
            page = MAINS_FEATURES[offset : offset + self.PAGE]
            exceeded = (offset + self.PAGE) < len(MAINS_FEATURES)
            self.mains_pages_served += 1
            return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}
        if layer in NODE_FIXTURES:
            wanted = set(_parse_in_ids(params.get("where", "")))
            feats = [f for f in NODE_FIXTURES[layer]
                     if f["properties"]["GIS_FeatureKey"] in wanted]
            return {"type": "FeatureCollection", "features": feats}
        return {"type": "FeatureCollection", "features": []}


def test_pagination_concatenates_all_pages():
    client = PagingClient()
    res = fetch_london_storm(BBOX, client=client)
    assert client.mains_pages_served >= 2, "expected the fetcher to request a second page"
    assert len(res["mains"]) == len(MAINS_FEATURES)
    ids = [f["properties"]["GIS_FeatureKey"] for f in res["mains"]]
    assert len(ids) == len(set(ids))


def test_pagination_offsets_advance():
    client = PagingClient()
    fetch_london_storm(BBOX, client=client)
    offsets = [int(p.get("resultOffset", 0) or 0) for (layer, p) in client.calls if layer == PIPES]
    assert offsets[0] == 0
    assert max(offsets) > 0, "later pipe pages must use a non-zero resultOffset"


# --- defensive behaviour -----------------------------------------------------

class EmptyNodesClient(FakeClient):
    """Pipes resolve normally; every node layer returns zero features."""

    def get_json(self, url, params):
        layer = _layer_id(url)
        if layer in NODE_FIXTURES:
            self.calls.append((layer, params))
            return {"type": "FeatureCollection", "features": []}
        return super().get_json(url, params)


def test_tolerates_node_layer_with_zero_features():
    res = fetch_london_storm(BBOX, client=EmptyNodesClient())
    assert res["mains"]
    assert res["manholes"] == [] and res["other_nodes"] == [] and res["outfalls"] == []


def test_chunks_featurekey_in_at_most_80_ids():
    client = FakeClient()
    fetch_london_storm(BBOX, client=client)
    for layer, params in client.calls:
        if layer in NODE_FIXTURES:
            assert len(_parse_in_ids(params["where"])) <= 80


# --- Esri-JSON conversion path (f=json fallback) -----------------------------

def test_esri_json_mains_convert_to_geojson_features():
    """The captured raw f=json mains page (Esri JSON: attributes + geometry.paths) converts
    to GeoJSON Features via base.esri_to_geojson — the documented fallback for any layer that
    serves only Esri JSON, and the parse path behind the geojson fixtures."""
    raw = json.loads((FIX / "raw_arcgis_mains_query.json").read_text())
    assert raw["features"], "expected an Esri-JSON mains page"
    assert raw.get("exceededTransferLimit") is True  # provenance for the pagination contract
    for feat in raw["features"]:
        assert "attributes" in feat and "paths" in feat["geometry"]   # Esri JSON, not GeoJSON
        gj = base.esri_to_geojson(feat)
        assert gj["type"] == "Feature"
        assert gj["geometry"]["type"] in ("LineString", "MultiLineString")
        assert gj["geometry"]["coordinates"]
        assert gj["properties"]["FlowType"] == "STM"
        assert gj["properties"]["GIS_FeatureKey"]
