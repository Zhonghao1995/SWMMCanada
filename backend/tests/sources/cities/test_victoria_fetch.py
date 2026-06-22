"""TDD for sources.victoria_fetch: offline replay of recorded City-of-Victoria
ArcGIS responses through an injected fake client (mirrors test_climate.py's pattern).

Fetch contract under test (see tests/fixtures/victoria/README.md):
  1. MAINS (layer 10) filtered to WaterType='STM', bbox envelope, paginated.
  2. Node ids harvested from Upstream/DownstreamNodeID, split by prefix
     DMH->manholes(4) / DFG->fittings(3) / DOF->outfalls(5).
  3. Nodes fetched BY AssetID (where=AssetID IN (...)), chunked — NOT by bbox.
"""
import json
import re
from pathlib import Path

import pytest

from swmmcanada.sources.cities.victoria import (
    FITTINGS,
    MAINS,
    MANHOLES,
    OUTFALLS,
    fetch_victoria_storm,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "victoria"

# A bbox loosely covering the captured downtown AOI (EPSG:4326 lon/lat). The fake
# client ignores the actual envelope numbers — geometry filtering is the server's job
# in production; here we only assert the *request shape*.
BBOX = (-123.380, 48.415, -123.360, 48.430)


def _load(name):
    return json.loads((FIX / f"{name}.geojson").read_text())["features"]


MAINS_FEATURES = _load("mains")
NODE_FIXTURES = {
    MANHOLES: _load("manholes"),
    FITTINGS: _load("fittings"),
    OUTFALLS: _load("outfalls"),
}


def _layer_id(url):
    """Trailing '/<id>/query' segment of an ArcGIS layer query URL."""
    m = re.search(r"/(\d+)/query/?$", url)
    return int(m.group(1)) if m else None


def _parse_in_ids(where):
    """Pull the quoted ids out of a `AssetID IN ('a','b',...)` clause."""
    return re.findall(r"'([^']+)'", where.split("IN", 1)[1])


class FakeClient:
    """Replays the *.geojson fixtures as if they were live f=geojson responses.

    Records every (layer_id, where) it is asked for so tests can assert the
    request shape: STM filter on mains, AssetID-IN (not bbox) on nodes.
    """

    def __init__(self):
        self.calls = []  # list of (layer_id, params)

    def get_json(self, url, params):
        layer = _layer_id(url)
        where = params.get("where", "")
        self.calls.append((layer, params))

        if layer == MAINS:
            # Honour the WaterType filter so the test proves filtering happens,
            # and honour resultOffset/resultRecordCount so pagination is exercised.
            feats = MAINS_FEATURES
            if "WaterType" in where:
                want = re.search(r"WaterType\s*=\s*'([^']+)'", where).group(1)
                feats = [f for f in feats if f["properties"].get("WaterType") == want]
            offset = int(params.get("resultOffset", 0) or 0)
            count = params.get("resultRecordCount")
            page = feats[offset:] if count is None else feats[offset : offset + int(count)]
            exceeded = count is not None and (offset + int(count)) < len(feats)
            return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}

        if layer in NODE_FIXTURES:
            assert "AssetID IN" in where, f"node layer {layer} must be queried by AssetID, got: {where!r}"
            wanted = set(_parse_in_ids(where))
            feats = [f for f in NODE_FIXTURES[layer] if f["properties"]["AssetID"] in wanted]
            return {"type": "FeatureCollection", "features": feats}

        return {"type": "FeatureCollection", "features": []}


# --- helpers to compute the expected join from the fixtures -------------------

def _referenced_ids_by_prefix(prefix):
    ids = set()
    for f in MAINS_FEATURES:
        for key in ("UpstreamNodeID", "DownstreamNodeID"):
            v = f["properties"].get(key)
            if v and v.startswith(prefix):
                ids.add(v)
    return ids


def _fixture_ids(layer):
    return {f["properties"]["AssetID"] for f in NODE_FIXTURES[layer]}


# --- tests -------------------------------------------------------------------

def test_returns_four_keys_as_geojson_features():
    res = fetch_victoria_storm(BBOX, client=FakeClient())
    assert set(res) == {"mains", "manholes", "fittings", "outfalls"}
    for key in res:
        assert isinstance(res[key], list)
    for f in res["mains"]:
        assert f["type"] == "Feature"
        assert "properties" in f and "geometry" in f


def test_mains_filtered_to_stm():
    client = FakeClient()
    res = fetch_victoria_storm(BBOX, client=client)
    assert res["mains"], "expected STM mains from the fixture"
    assert all(f["properties"]["WaterType"] == "STM" for f in res["mains"])
    # The mains request must carry the STM where-clause.
    mains_calls = [p for (layer, p) in client.calls if layer == MAINS]
    assert mains_calls, "mains layer was never queried"
    assert any("WaterType" in c.get("where", "") and "STM" in c.get("where", "") for c in mains_calls)


def test_mains_request_uses_envelope_geometry_filter():
    client = FakeClient()
    fetch_victoria_storm(BBOX, client=client)
    first = next(p for (layer, p) in client.calls if layer == MAINS)
    assert first.get("geometryType") == "esriGeometryEnvelope"
    assert first.get("spatialRel") == "esriSpatialRelIntersects"
    # Envelope is minx,miny,maxx,maxy from the bbox.
    assert first.get("geometry") == "-123.38,48.415,-123.36,48.43"
    assert str(first.get("inSR")) == "4326"
    assert first.get("f") == "geojson"


def test_nodes_fetched_by_assetid_not_bbox():
    client = FakeClient()
    fetch_victoria_storm(BBOX, client=client)
    node_calls = [(layer, p) for (layer, p) in client.calls if layer in NODE_FIXTURES]
    assert node_calls, "no node layers were queried"
    for layer, params in node_calls:
        where = params.get("where", "")
        assert "AssetID IN" in where, f"layer {layer} not queried by AssetID: {where!r}"
        # The fix for dangling edge nodes: nodes are NOT pulled by an envelope.
        assert "geometry" not in params or not params.get("geometry")
        assert params.get("geometryType") in (None, "")


def test_returned_nodes_cover_referenced_ids_present_in_fixtures():
    res = fetch_victoria_storm(BBOX, client=FakeClient())
    for layer, key, prefix in (
        (MANHOLES, "manholes", "DMH"),
        (FITTINGS, "fittings", "DFG"),
        (OUTFALLS, "outfalls", "DOF"),
    ):
        referenced = _referenced_ids_by_prefix(prefix)
        present = referenced & _fixture_ids(layer)  # ~10% of refs dangle (absent)
        got = {f["properties"]["AssetID"] for f in res[key]}
        assert got == present, f"{key}: expected {len(present)} nodes, got {len(got)}"
        for f in res[key]:
            assert f["type"] == "Feature" and f["geometry"]["type"] == "Point"


def test_nodes_are_deduped_by_assetid():
    res = fetch_victoria_storm(BBOX, client=FakeClient())
    for key in ("manholes", "fittings", "outfalls"):
        ids = [f["properties"]["AssetID"] for f in res[key]]
        assert len(ids) == len(set(ids)), f"{key} contains duplicate AssetIDs"


def test_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_victoria_storm(AOI(), client=FakeClient())
    assert res["mains"] and res["manholes"]


# --- pagination --------------------------------------------------------------

class PagingClient:
    """First mains page reports exceededTransferLimit=True, second page is smaller.

    Splits the fixture mains into two pages regardless of the requested
    resultRecordCount, to prove the fetcher concatenates pages until the limit
    is no longer exceeded. Nodes resolve from the fixtures as usual.
    """

    PAGE = 30  # fixture has 50 mains -> page1=30 (exceeded), page2=20 (done)

    def __init__(self):
        self.mains_pages_served = 0
        self.calls = []

    def get_json(self, url, params):
        layer = _layer_id(url)
        self.calls.append((layer, params))
        if layer == MAINS:
            offset = int(params.get("resultOffset", 0) or 0)
            page = MAINS_FEATURES[offset : offset + self.PAGE]
            exceeded = (offset + self.PAGE) < len(MAINS_FEATURES)
            self.mains_pages_served += 1
            return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}
        if layer in NODE_FIXTURES:
            wanted = set(_parse_in_ids(params.get("where", "")))
            feats = [f for f in NODE_FIXTURES[layer] if f["properties"]["AssetID"] in wanted]
            return {"type": "FeatureCollection", "features": feats}
        return {"type": "FeatureCollection", "features": []}


def test_pagination_concatenates_all_pages():
    client = PagingClient()
    res = fetch_victoria_storm(BBOX, client=client)
    assert client.mains_pages_served >= 2, "expected the fetcher to request a second page"
    assert len(res["mains"]) == len(MAINS_FEATURES)  # all 50 concatenated
    ids = [f["properties"]["AssetID"] for f in res["mains"]]
    assert len(ids) == len(set(ids))


def test_pagination_offsets_advance():
    client = PagingClient()
    fetch_victoria_storm(BBOX, client=client)
    offsets = [int(p.get("resultOffset", 0) or 0) for (layer, p) in client.calls if layer == MAINS]
    assert offsets[0] == 0
    assert max(offsets) > 0, "later mains pages must use a non-zero resultOffset"


# --- defensive behaviour -----------------------------------------------------

class EmptyNodesClient(FakeClient):
    """Mains resolve normally; every node layer returns zero features."""

    def get_json(self, url, params):
        layer = _layer_id(url)
        if layer in NODE_FIXTURES:
            self.calls.append((layer, params))
            return {"type": "FeatureCollection", "features": []}
        return super().get_json(url, params)


def test_tolerates_node_layer_with_zero_features():
    res = fetch_victoria_storm(BBOX, client=EmptyNodesClient())
    assert res["mains"]
    assert res["manholes"] == [] and res["fittings"] == [] and res["outfalls"] == []


def test_chunks_assetid_in_at_most_80_ids():
    client = FakeClient()
    fetch_victoria_storm(BBOX, client=client)
    for layer, params in client.calls:
        if layer in NODE_FIXTURES:
            assert len(_parse_in_ids(params["where"])) <= 80
