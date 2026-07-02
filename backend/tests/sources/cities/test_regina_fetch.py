"""Offline replay of recorded City-of-Regina ArcGIS responses through an injected fake
client (mirrors test_kelowna_fetch.py / test_surrey_fetch.py patterns).

Regina's own ArcGIS Server serves real geometry for ``f=geojson`` (verified 2026-07-02), so
the adapter reads GeoJSON directly; an Esri-JSON fallback is kept defensively. These tests
assert the *request shape* (bbox envelope, f=geojson, the ACTIVE/non-Force pipe where-clause,
pagination) and that storm + land fetches return the right keys and hit the right layers/
services.
"""
import json
import re
from pathlib import Path

from swmmcanada.sources.cities.regina import (
    _PIPES_WHERE,
    STORM_CATCHBASINS,
    STORM_MANHOLES,
    STORM_OUTFALLS,
    STORM_PIPES,
    build_regina_network,
    fetch_regina_land,
    fetch_regina_storm,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "regina"

# A bbox loosely covering the captured sub-area (EPSG:4326 lon/lat). The fake client ignores
# the actual envelope numbers; we only assert the request shape.
BBOX = (-104.622, 50.4355, -104.612, 50.447)


def _load(name):
    return json.loads((FIX / name).read_text())["features"]


PIPES = _load("storm_pipes.geojson")
OUTFALLS = _load("outfalls.geojson")
CATCHBASINS = _load("catchbasins.geojson")
MANHOLES = _load("manholes.geojson")


def _layer_id(url):
    m = re.search(r"/(\d+)/query/?$", url)
    return int(m.group(1)) if m else None


def _is_storm(url):
    return "/StormSewerNetwork/MapServer" in url


class FakeClient:
    """Replays the fixtures as if they were live f=geojson responses, honouring
    resultOffset/resultRecordCount so pagination is exercised. Records (url, layer, params) for
    request-shape assertions. Parcels/buildings (their own services) return a small stub each."""

    def __init__(self):
        self.calls = []  # list of (url, layer_id, params)

    def get_json(self, url, params):
        layer = _layer_id(url)
        self.calls.append((url, layer, params))

        if not _is_storm(url):
            # Two stub polygons each so parcels/buildings come back non-empty.
            kind = "parcel" if "/Parcels/MapServer" in url else "building"
            feats = [
                {"type": "Feature", "properties": {"OBJECTID": i, "kind": kind},
                 "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
                for i in range(2)
            ]
            return {"type": "FeatureCollection", "features": feats}

        src = {STORM_PIPES: PIPES, STORM_OUTFALLS: OUTFALLS, STORM_CATCHBASINS: CATCHBASINS,
               STORM_MANHOLES: MANHOLES}.get(layer, [])
        offset = int(params.get("resultOffset", 0) or 0)
        count = params.get("resultRecordCount")
        page = src[offset:] if count is None else src[offset : offset + int(count)]
        exceeded = count is not None and (offset + int(count)) < len(src)
        return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}


# --- storm fetch -----------------------------------------------------------------

def test_storm_returns_pipes_and_outfalls_as_features():
    res = fetch_regina_storm(BBOX, client=FakeClient())
    assert set(res) == {"pipes", "outfalls", "manholes"}   # manholes -> rim depths (refs #53)
    assert res["pipes"] and res["outfalls"] and res["manholes"]
    for f in res["pipes"]:
        assert f["type"] == "Feature"
        assert "properties" in f and "geometry" in f
        assert f["geometry"]["type"] == "LineString"


def test_storm_pipes_filtered_to_active_gravity_lines():
    """The pipe query must carry the ACTIVE + non-Force where-clause (abandoned / cut-off /
    not-in-use lines and pressurized force mains are not part of the gravity storm graph)."""
    client = FakeClient()
    fetch_regina_storm(BBOX, client=client)
    pipe_wheres = [p.get("where", "") for (url, layer, p) in client.calls if layer == STORM_PIPES]
    assert pipe_wheres and all(w == _PIPES_WHERE for w in pipe_wheres)
    assert "STATUS = 'ACTIVE'" in _PIPES_WHERE and "'Force'" in _PIPES_WHERE
    outfall_wheres = [p.get("where", "") for (url, layer, p) in client.calls if layer == STORM_OUTFALLS]
    assert outfall_wheres and all(w == "1=1" for w in outfall_wheres)


def test_storm_request_uses_geojson_envelope_filter():
    client = FakeClient()
    fetch_regina_storm(BBOX, client=client)
    pipe_calls = [p for (url, layer, p) in client.calls if layer == STORM_PIPES]
    assert pipe_calls, "storm sewer line layer was never queried"
    first = pipe_calls[0]
    assert first.get("geometryType") == "esriGeometryEnvelope"
    assert first.get("spatialRel") == "esriSpatialRelIntersects"
    assert first.get("geometry") == "-104.622,50.4355,-104.612,50.447"
    assert str(first.get("inSR")) == "4326"
    assert first.get("f") == "geojson"           # Regina serves real geojson


def test_storm_hits_pipe_and_outfall_layers_on_storm_service():
    client = FakeClient()
    fetch_regina_storm(BBOX, client=client)
    layers = {layer for (url, layer, p) in client.calls}
    assert STORM_PIPES in layers and STORM_OUTFALLS in layers
    assert all(_is_storm(url) for (url, layer, p) in client.calls)


def test_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_regina_storm(AOI(), client=FakeClient())
    assert res["pipes"] and res["outfalls"]


# --- land fetch (Regina HAS parcels + buildings) ----------------------------------

def test_land_returns_catchbasins_parcels_buildings():
    res = fetch_regina_land(BBOX, client=FakeClient())
    assert set(res) == {"catchbasins", "parcels", "buildings"}
    assert res["catchbasins"], "expected catch basins from the fixture"
    assert res["parcels"], "Regina publishes lot-level parcels -> must be fetched"
    assert res["buildings"], "Regina publishes building footprints -> must be fetched"


def test_land_catchbasins_carry_rim_and_sump():
    res = fetch_regina_land(BBOX, client=FakeClient())
    p = res["catchbasins"][0]["properties"]
    assert "RIMELEVATION" in p and "SUMPELEVATION" in p


def test_land_fetches_parcels_buildings_from_own_services():
    client = FakeClient()
    fetch_regina_land(BBOX, client=client)
    assert any("/Parcels/MapServer" in url for (url, layer, p) in client.calls)
    assert any("/BuildingFootprint/MapServer" in url for (url, layer, p) in client.calls)
    cb_calls = [url for (url, layer, p) in client.calls if layer == STORM_CATCHBASINS]
    assert cb_calls and all(_is_storm(url) for url in cb_calls)


# --- pagination ------------------------------------------------------------------

class PagingClient:
    """First storm-line page reports exceededTransferLimit=True, then a smaller final page,
    to prove the fetcher concatenates pages until the limit clears. Other layers resolve in one
    page."""

    PAGE = 100  # fixture has 178 lines -> page1=100 (exceeded), page2=78 (done)

    def __init__(self):
        self.pipe_pages_served = 0
        self.calls = []

    def get_json(self, url, params):
        layer = _layer_id(url)
        self.calls.append((url, layer, params))
        if not _is_storm(url):
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
    res = fetch_regina_storm(BBOX, client=client)
    assert client.pipe_pages_served >= 2, "expected the fetcher to request a second page"
    assert len(res["pipes"]) == len(PIPES)   # all 178 concatenated


def test_pagination_offsets_advance():
    client = PagingClient()
    fetch_regina_storm(BBOX, client=client)
    offsets = [int(p.get("resultOffset", 0) or 0) for (url, layer, p) in client.calls if layer == STORM_PIPES]
    assert offsets[0] == 0
    assert max(offsets) > 0, "later pipe pages must use a non-zero resultOffset"


# --- esri-json -> geojson fallback ------------------------------------------------

class EsriJsonClient:
    """Serves the pipe layer as Esri JSON (attributes/paths) to prove the defensive
    esri_to_geojson fallback fires when a layer doesn't honour f=geojson."""

    def get_json(self, url, params):
        if _layer_id(url) == STORM_PIPES and _is_storm(url):
            feats = [
                {"attributes": dict(f["properties"]),
                 "geometry": {"paths": [f["geometry"]["coordinates"]]}}
                for f in PIPES[:10]
            ]
            return {"features": feats}
        return {"type": "FeatureCollection", "features": []}


def test_esri_json_features_are_converted_to_geojson():
    res = fetch_regina_storm(BBOX, client=EsriJsonClient())
    assert len(res["pipes"]) == 10
    for f in res["pipes"]:
        assert f["type"] == "Feature"
        assert f["geometry"]["type"] == "LineString"
        assert "GISID" in f["properties"]


def test_converted_esri_network_still_builds():
    """The esri->geojson path must produce features build_regina_network can consume."""
    res = fetch_regina_storm(BBOX, client=EsriJsonClient())
    out = build_regina_network(res)
    assert out.network.conduits
