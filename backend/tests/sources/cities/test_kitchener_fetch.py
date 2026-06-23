"""TDD for sources.cities.kitchener fetch: offline replay of recorded Region-of-Waterloo
ArcGIS responses through an injected fake client (mirrors the Victoria fetch tests).

Fetch contract under test (see tests/fixtures/kitchener/README.md):
  1. Storm_Pipes by bbox envelope, paginated (f=geojson).
  2. Manhole ids harvested from UP_STMMANHOLEID/DN_STMMANHOLEID (positive ints only; -1 dropped).
  3. Manholes fetched BY STMMANHOLEID (where=STMMANHOLEID IN (...)), chunked — NOT by bbox.
  4. Storm_Outlets by bbox envelope.
  5. fetch_kitchener_land returns catchbasins + buildings by bbox, and parcels == [] (none exist).
"""
import json
import re
from pathlib import Path

from swmmcanada.sources.cities.kitchener import (
    fetch_kitchener_land,
    fetch_kitchener_storm,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "kitchener"

# A bbox loosely covering the captured AOI (EPSG:4326). The fake client ignores the actual
# envelope numbers — geometry filtering is the server's job in production; here we only assert
# the *request shape*.
BBOX = (-80.4925, 43.4385, -80.4810, 43.4445)


def _load(name):
    return json.loads((FIX / f"{name}.geojson").read_text())["features"]


PIPES = _load("pipes")
MANHOLES = _load("manholes")
OUTLETS = _load("outlets")
CATCHBASINS = _load("catchbasins")
BUILDINGS = _load("buildings")


def _layer_name(url):
    """The service name segment, e.g. '.../Storm_Pipes/FeatureServer/0/query' -> 'Storm_Pipes'."""
    m = re.search(r"/services/([^/]+)/FeatureServer/", url)
    return m.group(1) if m else None


def _parse_in_ids(where):
    return re.findall(r"\d+", where.split("IN", 1)[1])


class FakeClient:
    """Replays the *.geojson fixtures as live f=geojson responses, recording every
    (layer_name, params) so tests can assert the request shape."""

    def __init__(self):
        self.calls = []

    def _bbox_layer(self, feats, params):
        offset = int(params.get("resultOffset", 0) or 0)
        count = params.get("resultRecordCount")
        page = feats[offset:] if count is None else feats[offset: offset + int(count)]
        exceeded = count is not None and (offset + int(count)) < len(feats)
        return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}

    def get_json(self, url, params):
        layer = _layer_name(url)
        self.calls.append((layer, params))
        if layer == "Storm_Pipes":
            return self._bbox_layer(PIPES, params)
        if layer == "Storm_Outlets":
            return self._bbox_layer(OUTLETS, params)
        if layer == "Storm_Catchbasins":
            return self._bbox_layer(CATCHBASINS, params)
        if layer == "Building_Outlines":
            return self._bbox_layer(BUILDINGS, params)
        if layer == "Storm_Manholes":
            where = params.get("where", "")
            assert "STMMANHOLEID IN" in where, f"manholes must be queried by id, got: {where!r}"
            wanted = {int(x) for x in _parse_in_ids(where)}
            feats = [f for f in MANHOLES if f["properties"]["STMMANHOLEID"] in wanted]
            return {"type": "FeatureCollection", "features": feats}
        return {"type": "FeatureCollection", "features": []}


# --- helpers to compute the expected join from the fixtures -------------------

def _referenced_manhole_ids():
    ids = set()
    for f in PIPES:
        for key in ("UP_STMMANHOLEID", "DN_STMMANHOLEID"):
            v = f["properties"].get(key)
            if v is not None and int(v) > 0:
                ids.add(int(v))
    return ids


def _fixture_manhole_ids():
    return {f["properties"]["STMMANHOLEID"] for f in MANHOLES}


# --- storm fetch -------------------------------------------------------------

def test_returns_three_keys_as_geojson_features():
    res = fetch_kitchener_storm(BBOX, client=FakeClient())
    assert set(res) == {"pipes", "manholes", "outlets"}
    for key in res:
        assert isinstance(res[key], list)
    for f in res["pipes"]:
        assert f["type"] == "Feature"
        assert "properties" in f and "geometry" in f
        assert f["geometry"]["type"] in ("LineString", "MultiLineString")


def test_pipes_request_uses_envelope_geometry_filter():
    client = FakeClient()
    fetch_kitchener_storm(BBOX, client=client)
    first = next(p for (layer, p) in client.calls if layer == "Storm_Pipes")
    assert first.get("geometryType") == "esriGeometryEnvelope"
    assert first.get("spatialRel") == "esriSpatialRelIntersects"
    assert first.get("geometry") == "-80.4925,43.4385,-80.481,43.4445"
    assert str(first.get("inSR")) == "4326"
    assert first.get("f") == "geojson"


def test_manholes_fetched_by_id_not_bbox():
    client = FakeClient()
    fetch_kitchener_storm(BBOX, client=client)
    mh_calls = [(layer, p) for (layer, p) in client.calls if layer == "Storm_Manholes"]
    assert mh_calls, "manhole layer was never queried"
    for _, params in mh_calls:
        where = params.get("where", "")
        assert "STMMANHOLEID IN" in where, f"manholes not queried by id: {where!r}"
        # The fix for dangling edge nodes: manholes are NOT pulled by an envelope.
        assert "geometry" not in params or not params.get("geometry")
        assert params.get("geometryType") in (None, "")


def test_returned_manholes_cover_referenced_ids_present_in_fixtures():
    res = fetch_kitchener_storm(BBOX, client=FakeClient())
    referenced = _referenced_manhole_ids()
    present = referenced & _fixture_manhole_ids()
    got = {f["properties"]["STMMANHOLEID"] for f in res["manholes"]}
    assert got == present, f"expected {len(present)} manholes, got {len(got)}"
    for f in res["manholes"]:
        assert f["type"] == "Feature" and f["geometry"]["type"] == "Point"


def test_sentinel_negative_ids_are_not_queried():
    """The -1 sentinel (no-manhole) must never appear in the STMMANHOLEID IN-list."""
    client = FakeClient()
    fetch_kitchener_storm(BBOX, client=client)
    for layer, params in client.calls:
        if layer == "Storm_Manholes":
            ids = _parse_in_ids(params["where"])
            assert all(int(i) > 0 for i in ids)
            assert "-1" not in params["where"]


def test_manholes_deduped_by_id():
    res = fetch_kitchener_storm(BBOX, client=FakeClient())
    ids = [f["properties"]["STMMANHOLEID"] for f in res["manholes"]]
    assert len(ids) == len(set(ids))


def test_outlets_fetched_by_bbox():
    client = FakeClient()
    res = fetch_kitchener_storm(BBOX, client=client)
    assert res["outlets"], "expected outlets from the fixture"
    outlet_calls = [p for (layer, p) in client.calls if layer == "Storm_Outlets"]
    assert outlet_calls
    assert outlet_calls[0].get("geometryType") == "esriGeometryEnvelope"
    for f in res["outlets"]:
        assert f["geometry"]["type"] == "Point"


def test_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_kitchener_storm(AOI(), client=FakeClient())
    assert res["pipes"] and res["manholes"]


def test_chunks_id_in_at_most_80():
    client = FakeClient()
    fetch_kitchener_storm(BBOX, client=client)
    for layer, params in client.calls:
        if layer == "Storm_Manholes":
            assert len(_parse_in_ids(params["where"])) <= 80


# --- pagination --------------------------------------------------------------

class PagingClient(FakeClient):
    """First pipes page reports exceededTransferLimit=True; splits the fixture pipes into pages
    regardless of resultRecordCount to prove the fetcher concatenates until the limit clears."""

    PAGE = 30

    def __init__(self):
        super().__init__()
        self.pipe_pages_served = 0

    def get_json(self, url, params):
        layer = _layer_name(url)
        if layer == "Storm_Pipes":
            self.calls.append((layer, params))
            offset = int(params.get("resultOffset", 0) or 0)
            page = PIPES[offset: offset + self.PAGE]
            exceeded = (offset + self.PAGE) < len(PIPES)
            self.pipe_pages_served += 1
            return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}
        return super().get_json(url, params)


def test_pagination_concatenates_all_pages():
    client = PagingClient()
    res = fetch_kitchener_storm(BBOX, client=client)
    assert client.pipe_pages_served >= 2, "expected a second pipes page"
    assert len(res["pipes"]) == len(PIPES)
    ids = [f["properties"]["STMPIPEID"] for f in res["pipes"]]
    assert len(ids) == len(set(ids))


def test_pagination_offsets_advance():
    client = PagingClient()
    fetch_kitchener_storm(BBOX, client=client)
    offsets = [int(p.get("resultOffset", 0) or 0) for (layer, p) in client.calls if layer == "Storm_Pipes"]
    assert offsets[0] == 0
    assert max(offsets) > 0


# --- land fetch --------------------------------------------------------------

def test_land_returns_catchbasins_and_buildings_with_empty_parcels():
    res = fetch_kitchener_land(BBOX, client=FakeClient())
    assert set(res) == {"catchbasins", "parcels", "buildings"}
    assert res["catchbasins"], "expected catch basins from the fixture"
    assert res["buildings"], "expected buildings from the fixture"
    # No parcel polygons are published for the Region of Waterloo org.
    assert res["parcels"] == []
    for f in res["catchbasins"]:
        assert f["geometry"]["type"] == "Point"
    for f in res["buildings"]:
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_land_does_not_query_a_parcel_layer():
    client = FakeClient()
    fetch_kitchener_land(BBOX, client=client)
    queried = {layer for (layer, _) in client.calls}
    assert "Property_Ownership_Public" not in queried
    assert "Storm_Catchbasins" in queried and "Building_Outlines" in queried


def test_land_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_kitchener_land(AOI(), client=FakeClient())
    assert res["catchbasins"] and res["buildings"]
