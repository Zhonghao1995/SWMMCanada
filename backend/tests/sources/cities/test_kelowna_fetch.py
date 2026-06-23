"""Offline replay of recorded City-of-Kelowna ArcGIS responses through an injected fake
client (mirrors test_victoria_fetch.py / test_ottawa patterns).

Kelowna's MapServer serves real geometry for ``f=geojson`` (verified 2026-06-22), so the
adapter reads GeoJSON directly (no esri_to_geojson). These tests assert the *request shape*
(bbox envelope, f=geojson, pagination) and that storm + land fetches return the right keys and
hit the right layers/services.
"""
import json
import re
from pathlib import Path

from swmmcanada.sources.cities.kelowna import (
    BUILDINGS,
    PARCELS,
    STORM_CATCHBASINS,
    STORM_OUTFALLS,
    STORM_PIPES,
    fetch_kelowna_land,
    fetch_kelowna_storm,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "kelowna"

# A bbox loosely covering the captured sub-area (EPSG:4326 lon/lat). The fake client ignores
# the actual envelope numbers; we only assert the request shape.
BBOX = (-119.475, 49.890, -119.469, 49.895)


def _load(name):
    return json.loads((FIX / name).read_text())["features"]


PIPES = _load("storm_pipes.geojson")
OUTFALLS = _load("outfalls.geojson")
CATCHBASINS = _load("catchbasins.geojson")


def _layer_id(url):
    m = re.search(r"/(\d+)/query/?$", url)
    return int(m.group(1)) if m else None


def _is_planning(url):
    return "OpenData_Planning_and_other" in url


class FakeClient:
    """Replays the fixtures as if they were live f=geojson responses, honouring
    resultOffset/resultRecordCount so pagination is exercised. Records (url, layer, params) for
    request-shape assertions. Parcels/buildings (planning service) return a small stub each."""

    def __init__(self):
        self.calls = []  # list of (url, layer_id, params)

    def get_json(self, url, params):
        layer = _layer_id(url)
        self.calls.append((url, layer, params))

        if _is_planning(url):
            # Two stub polygons each so parcels/buildings come back non-empty.
            kind = "parcel" if layer == PARCELS else "building"
            feats = [
                {"type": "Feature", "properties": {"OBJECTID": i, "kind": kind},
                 "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
                for i in range(2)
            ]
            return {"type": "FeatureCollection", "features": feats}

        src = {STORM_PIPES: PIPES, STORM_OUTFALLS: OUTFALLS, STORM_CATCHBASINS: CATCHBASINS}.get(layer, [])
        offset = int(params.get("resultOffset", 0) or 0)
        count = params.get("resultRecordCount")
        page = src[offset:] if count is None else src[offset : offset + int(count)]
        exceeded = count is not None and (offset + int(count)) < len(src)
        return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}


# --- storm fetch -----------------------------------------------------------------

def test_storm_returns_pipes_and_outfalls_as_features():
    res = fetch_kelowna_storm(BBOX, client=FakeClient())
    assert set(res) == {"pipes", "outfalls"}
    assert res["pipes"] and res["outfalls"]
    for f in res["pipes"]:
        assert f["type"] == "Feature"
        assert "properties" in f and "geometry" in f
        assert f["geometry"]["type"] == "LineString"


def test_storm_extracts_string_fields_unchanged():
    """The fetcher passes raw attributes through; DIAMETER/LENGTH stay strings here (the
    network builder is what casts them)."""
    res = fetch_kelowna_storm(BBOX, client=FakeClient())
    p = res["pipes"][0]["properties"]
    assert isinstance(p["DIAMETER"], str)
    assert isinstance(p["LENGTH"], str)
    assert "INVERT_IN_Z" in p and "INVERT_OUT_Z" in p


def test_storm_request_uses_geojson_envelope_filter():
    client = FakeClient()
    fetch_kelowna_storm(BBOX, client=client)
    pipe_calls = [p for (url, layer, p) in client.calls if layer == STORM_PIPES]
    assert pipe_calls, "storm main layer was never queried"
    first = pipe_calls[0]
    assert first.get("geometryType") == "esriGeometryEnvelope"
    assert first.get("spatialRel") == "esriSpatialRelIntersects"
    assert first.get("geometry") == "-119.475,49.89,-119.469,49.895"
    assert str(first.get("inSR")) == "4326"
    assert first.get("f") == "geojson"           # Kelowna serves real geojson


def test_storm_hits_pipe_and_outfall_layers_on_storm_service():
    client = FakeClient()
    fetch_kelowna_storm(BBOX, client=client)
    layers = {layer for (url, layer, p) in client.calls}
    assert STORM_PIPES in layers and STORM_OUTFALLS in layers
    assert all("OpenData_Utilities_Storm" in url for (url, layer, p) in client.calls)


def test_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_kelowna_storm(AOI(), client=FakeClient())
    assert res["pipes"] and res["outfalls"]


# --- land fetch (Kelowna HAS parcels + buildings) --------------------------------

def test_land_returns_catchbasins_parcels_buildings():
    res = fetch_kelowna_land(BBOX, client=FakeClient())
    assert set(res) == {"catchbasins", "parcels", "buildings"}
    assert res["catchbasins"], "expected catch basins from the fixture"
    assert res["parcels"], "Kelowna publishes parcels -> must be fetched"
    assert res["buildings"], "Kelowna publishes building outlines -> must be fetched"


def test_land_catchbasins_carry_sump_and_type():
    res = fetch_kelowna_land(BBOX, client=FakeClient())
    p = res["catchbasins"][0]["properties"]
    assert "SUMP_ELEVATION" in p and "CB_TYPE" in p


def test_land_fetches_parcels_buildings_from_planning_service():
    client = FakeClient()
    fetch_kelowna_land(BBOX, client=client)
    planning_layers = {layer for (url, layer, p) in client.calls if _is_planning(url)}
    assert planning_layers == {PARCELS, BUILDINGS}
    cb_calls = [(url, layer) for (url, layer, p) in client.calls if layer == STORM_CATCHBASINS]
    assert cb_calls and all("OpenData_Utilities_Storm" in url for url, _ in cb_calls)


# --- pagination ------------------------------------------------------------------

class PagingClient:
    """First storm-main page reports exceededTransferLimit=True, then a smaller final page,
    to prove the fetcher concatenates pages until the limit clears. Other layers resolve in one
    page."""

    PAGE = 30  # fixture has 46 mains -> page1=30 (exceeded), page2=16 (done)

    def __init__(self):
        self.pipe_pages_served = 0
        self.calls = []

    def get_json(self, url, params):
        layer = _layer_id(url)
        self.calls.append((url, layer, params))
        if "OpenData_Planning_and_other" in url:
            return {"type": "FeatureCollection", "features": []}
        if layer == STORM_PIPES:
            offset = int(params.get("resultOffset", 0) or 0)
            page = PIPES[offset : offset + self.PAGE]
            exceeded = (offset + self.PAGE) < len(PIPES)
            self.pipe_pages_served += 1
            return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}
        src = {STORM_OUTFALLS: OUTFALLS, STORM_CATCHBASINS: CATCHBASINS}.get(layer, [])
        return {"type": "FeatureCollection", "features": src}


def test_pagination_concatenates_all_pages():
    client = PagingClient()
    res = fetch_kelowna_storm(BBOX, client=client)
    assert client.pipe_pages_served >= 2, "expected the fetcher to request a second page"
    assert len(res["pipes"]) == len(PIPES)   # all 46 concatenated


def test_pagination_offsets_advance():
    client = PagingClient()
    fetch_kelowna_storm(BBOX, client=client)
    offsets = [int(p.get("resultOffset", 0) or 0) for (url, layer, p) in client.calls if layer == STORM_PIPES]
    assert offsets[0] == 0
    assert max(offsets) > 0, "later pipe pages must use a non-zero resultOffset"
