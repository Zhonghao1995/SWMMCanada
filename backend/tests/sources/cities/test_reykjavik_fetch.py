"""Offline replay of the Reykjavík/LÚKK fetch layer through an injected fake client (mirrors the
Calgary/Ottawa fetch tests). No network: the fake client serves the checked-in fixtures.

Fetch contract under test:
  1. Mains + structures by bbox envelope, paginated (resultOffset advances until
     exceededTransferLimit clears).
  2. The adapter requests **Esri JSON** (f=json) and converts via base.esri_to_geojson — NOT
     GeoJSON, because ArcGIS reports exceededTransferLimit top-level only for Esri JSON (a GeoJSON
     fetch silently stops after one page; regression-guarded by test_pagination_* below).
  3. INNIHALD splits the one mains layer: storm fetch keeps regnvatn+blandað (combined joins storm),
     sanitary fetch keeps skólp only — no pipe lost or double-counted.
  4. fetch_reykjavik_land returns catchbasins (Niðurföll) + empty parcels/buildings.
"""
import json
from pathlib import Path

from swmmcanada.sources.cities.reykjavik import (
    BUILDINGS_URL, INLETS_URL, PARCELS_URL, PIPES_URL, STRUCT_URL,
    fetch_reykjavik_land, fetch_reykjavik_sanitary, fetch_reykjavik_storm,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "reykjavik"
BBOX = (-21.940, 64.129, -21.937, 64.131)


def _load(name):
    return json.loads((FIX / name).read_text())["features"]


RAW_PIPES = _load("raw_pipes.geojson")
STRUCTS = _load("structures.geojson")
CATCHBASINS = _load("catchbasins.geojson")
PARCELS = _load("parcels.geojson")
BUILDINGS = _load("buildings.geojson")


def _to_esri(feat):
    """GeoJSON fixture -> the Esri JSON the server actually returns (attributes + paths/x,y)."""
    g = feat["geometry"]
    if g["type"] == "LineString":
        geom = {"paths": [g["coordinates"]]}
    elif g["type"] == "Point":
        geom = {"x": g["coordinates"][0], "y": g["coordinates"][1]}
    elif g["type"] == "Polygon":
        geom = {"rings": g["coordinates"]}
    else:
        geom = {}
    return {"attributes": dict(feat["properties"]), "geometry": geom}


class FakeClient:
    """Serves the fixtures as Esri JSON (the format the adapter requests), honouring
    resultOffset/resultRecordCount + a TOP-LEVEL exceededTransferLimit. Records every call."""

    def __init__(self, page=None):
        self.calls = []
        self.page = page

    def _layer(self, url):
        for tag, feats in ((PIPES_URL, RAW_PIPES), (STRUCT_URL, STRUCTS), (INLETS_URL, CATCHBASINS),
                           (PARCELS_URL, PARCELS), (BUILDINGS_URL, BUILDINGS)):
            if url.startswith(tag):
                return feats
        return []

    def get_json(self, url, params):
        self.calls.append((url, params))
        feats = self._layer(url)
        offset = int(params.get("resultOffset", 0) or 0)
        count = self.page if self.page is not None else params.get("resultRecordCount")
        count = int(count) if count is not None else None
        page = feats[offset:] if count is None else feats[offset: offset + count]
        exceeded = count is not None and (offset + count) < len(feats)
        return {"features": [_to_esri(f) for f in page], "exceededTransferLimit": exceeded}


# --- storm / sanitary split -------------------------------------------------------

def test_storm_returns_geojson_pipes_and_structures():
    res = fetch_reykjavik_storm(BBOX, client=FakeClient())
    assert set(res) == {"pipes", "structures"}
    assert res["pipes"] and res["structures"]
    for f in res["pipes"]:                              # Esri JSON was converted to GeoJSON
        assert f["type"] == "Feature" and f["geometry"]["type"] in ("LineString", "MultiLineString")
        assert "AUDKENNI" in f["properties"]


def test_storm_keeps_regnvatn_and_bland_but_drops_skolp():
    res = fetch_reykjavik_storm(BBOX, client=FakeClient())
    vals = {f["properties"]["INNIHALD"] for f in res["pipes"]}
    assert vals == {"regnvatn", "blandað"}             # combined joins storm; sanitary excluded


def test_sanitary_keeps_only_skolp():
    res = fetch_reykjavik_sanitary(BBOX, client=FakeClient())
    assert set(res) == {"pipes", "structures"}
    assert {f["properties"]["INNIHALD"] for f in res["pipes"]} == {"skólp"}


def test_storm_and_sanitary_partition_all_pipes_without_overlap():
    storm = fetch_reykjavik_storm(BBOX, client=FakeClient())["pipes"]
    san = fetch_reykjavik_sanitary(BBOX, client=FakeClient())["pipes"]
    s_ids = {f["properties"]["OBJECTID"] for f in storm}
    a_ids = {f["properties"]["OBJECTID"] for f in san}
    assert len(s_ids & a_ids) == 0                      # no pipe in both
    assert s_ids | a_ids == {f["properties"]["OBJECTID"] for f in RAW_PIPES}   # none lost


# --- request shape (Esri JSON, envelope) -----------------------------------------

def test_requests_use_envelope_and_esri_json():
    client = FakeClient()
    fetch_reykjavik_storm(BBOX, client=client)
    call = next(p for (u, p) in client.calls if u.startswith(PIPES_URL))
    assert call.get("geometryType") == "esriGeometryEnvelope"
    assert call.get("spatialRel") == "esriSpatialRelIntersects"
    assert call.get("geometry") == "-21.94,64.129,-21.937,64.131"
    assert str(call.get("inSR")) == "4326"
    assert call.get("f") == "json"                     # Esri JSON, NOT geojson (pagination reason)


def test_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_reykjavik_storm(AOI(), client=FakeClient())
    assert res["pipes"] and res["structures"]


# --- pagination (the fix for the 1000-feature silent truncation) -----------------

def test_pagination_concatenates_all_pages():
    client = FakeClient(page=2)                        # 6 raw pipes -> pages of 2
    res = fetch_reykjavik_storm(BBOX, client=client)
    got = {f["properties"]["OBJECTID"] for f in res["pipes"]} | \
        {f["properties"]["OBJECTID"] for f in fetch_reykjavik_sanitary(BBOX, client=FakeClient(page=2))["pipes"]}
    assert got == {1, 2, 3, 4, 5, 6}                   # every page retrieved, nothing truncated
    offsets = [int(p.get("resultOffset", 0) or 0) for (u, p) in client.calls if u.startswith(PIPES_URL)]
    assert offsets[0] == 0 and max(offsets) >= 4 and len(offsets) >= 3   # advanced through pages


# --- land -------------------------------------------------------------------------

def test_land_returns_catchbasins_parcels_and_buildings():
    res = fetch_reykjavik_land(BBOX, client=FakeClient())
    assert set(res) == {"catchbasins", "parcels", "buildings"}
    assert res["catchbasins"] and res["parcels"] and res["buildings"]
    assert all(f["geometry"]["type"] == "Point" for f in res["catchbasins"])
    assert all(f["geometry"]["type"] in ("Polygon", "MultiPolygon") for f in res["parcels"])
    client = FakeClient()
    fetch_reykjavik_land(BBOX, client=client)
    queried = {u.split("/FeatureServer")[0] for (u, _) in client.calls}
    assert all(any(url.startswith(q) for q in queried) for url in (INLETS_URL, PARCELS_URL, BUILDINGS_URL))


def test_land_repairs_invalid_building_footprints():
    """~1% of real Hús footprints are self-intersecting; fetch must repair (buffer(0)) so the
    downstream unary_union doesn't crash. The bowtie fixture must come back valid (or be dropped)."""
    from shapely.geometry import shape
    res = fetch_reykjavik_land(BBOX, client=FakeClient())
    assert all(shape(f["geometry"]).is_valid for f in res["buildings"])   # no invalid geometry escapes
    assert len(res["buildings"]) >= 2                                     # the two valid ones survive
