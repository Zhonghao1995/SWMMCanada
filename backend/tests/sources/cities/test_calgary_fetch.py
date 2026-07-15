"""Offline replay of recorded City-of-Calgary ArcGIS responses through an injected fake client
(mirrors the Victoria fetch tests). No network: the fake client serves the checked-in fixtures.

Fetch contract under test:
  1. Pipes/catch basins by bbox envelope, paginated (resultOffset advances until
     exceededTransferLimit clears).
  2. Outfalls filtered server-side to OUT_INLET IS NOT NULL (named receiving water body).
  3. f=geojson is requested; an Esri-JSON fallback (attributes + paths/x,y) is converted to
     GeoJSON via base.esri_to_geojson.
  4. fetch_calgary_land returns catchbasins + parcels + buildings (real polygon layers).
"""
import json
import re
from pathlib import Path

from swmmcanada.sources.cities.calgary import (
    _SANITARY_WHERE,
    BUILDINGS,
    PARCELS,
    SANITARY_PIPES,
    STORM_CATCHBASINS,
    STORM_INLET_OUTFALL,
    STORM_MANHOLES,
    STORM_PIPES,
    fetch_calgary_land,
    fetch_calgary_sanitary,
    fetch_calgary_storm,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "calgary"
BBOX = (-114.083, 51.051, -114.077, 51.055)


def _load(name):
    return json.loads((FIX / name).read_text())["features"]


PIPES = _load("storm_pipes.geojson")
OUTFALLS = _load("outfalls.geojson")
CATCHBASINS = _load("catchbasins.geojson")
MANHOLES = _load("manholes.geojson")
SAN_PIPES = _load("sanitary_pipes.geojson")


def _service(url):
    """The `/<Service>/FeatureServer/0/query` service name from a query URL."""
    m = re.search(r"/services/([^/]+)/FeatureServer/0/query/?$", url)
    return m.group(1) if m else None


class FakeClient:
    """Replays the *.geojson fixtures as f=geojson responses, honouring resultOffset /
    resultRecordCount so pagination is exercised. Records every (service, params)."""

    def __init__(self, page=None):
        self.calls = []
        self.page = page          # force a small page to drive pagination, else fixture-sized

    def _paginate(self, feats, params):
        offset = int(params.get("resultOffset", 0) or 0)
        count = self.page if self.page is not None else params.get("resultRecordCount")
        count = int(count) if count is not None else None
        page = feats[offset:] if count is None else feats[offset: offset + count]
        exceeded = count is not None and (offset + count) < len(feats)
        return {"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}

    def get_json(self, url, params):
        svc = _service(url)
        self.calls.append((svc, params))
        if svc == STORM_PIPES:
            return self._paginate(PIPES, params)
        if svc == STORM_INLET_OUTFALL:
            return self._paginate(OUTFALLS, params)
        if svc == STORM_MANHOLES:
            return self._paginate(MANHOLES, params)
        if svc == STORM_CATCHBASINS:
            return self._paginate(CATCHBASINS, params)
        if svc == SANITARY_PIPES:
            return self._paginate(SAN_PIPES, params)
        if svc in (PARCELS, BUILDINGS):
            return {"type": "FeatureCollection", "features": []}
        return {"type": "FeatureCollection", "features": []}


# --- storm fetch ----------------------------------------------------------------

def test_storm_returns_pipes_outfalls_and_manholes_as_geojson():
    res = fetch_calgary_storm(BBOX, client=FakeClient())
    assert set(res) == {"pipes", "outfalls", "manholes"}   # manholes -> rim depths
    assert res["pipes"] and res["outfalls"] and res["manholes"]
    for f in res["pipes"]:
        assert f["type"] == "Feature"
        assert "properties" in f and "geometry" in f
        assert f["geometry"]["type"] in ("LineString", "MultiLineString")


def test_storm_manholes_carry_rim_elevation():
    res = fetch_calgary_storm(BBOX, client=FakeClient())
    assert all(f["geometry"]["type"] == "Point" for f in res["manholes"])
    assert any("RIM_ELEV" in f["properties"] for f in res["manholes"])


def test_storm_requests_use_envelope_and_geojson():
    client = FakeClient()
    fetch_calgary_storm(BBOX, client=client)
    pipe_call = next(p for (svc, p) in client.calls if svc == STORM_PIPES)
    assert pipe_call.get("geometryType") == "esriGeometryEnvelope"
    assert pipe_call.get("spatialRel") == "esriSpatialRelIntersects"
    assert pipe_call.get("geometry") == "-114.083,51.051,-114.077,51.055"
    assert str(pipe_call.get("inSR")) == "4326"
    assert pipe_call.get("f") == "geojson"


def test_outfalls_filtered_to_named_water_body():
    """Outfalls must be requested with OUT_INLET IS NOT NULL (a feature is an outfall when it
    names a receiving water body; inlets have a null OUT_INLET)."""
    client = FakeClient()
    res = fetch_calgary_storm(BBOX, client=client)
    of_call = next(p for (svc, p) in client.calls if svc == STORM_INLET_OUTFALL)
    assert "OUT_INLET" in of_call.get("where", "")
    assert "NOT NULL" in of_call.get("where", "").upper()
    assert all(f["properties"].get("OUT_INLET") for f in res["outfalls"])


def test_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_calgary_storm(AOI(), client=FakeClient())
    assert res["pipes"] and res["outfalls"]


# --- sanitary fetch (second tagged system, ADR 0011) ------------------------------

def test_sanitary_returns_pipes_filtered_to_active_gravity():
    """The sanitary query must hit SANITARY_PIPE with the ACTIVE + MAIN/TL where-clause
    (force mains, syphons and service laterals are not part of the gravity skeleton)."""
    client = FakeClient()
    res = fetch_calgary_sanitary(BBOX, client=client)
    assert set(res) == {"pipes"}
    assert res["pipes"]
    san_wheres = [p.get("where", "") for (svc, p) in client.calls if svc == SANITARY_PIPES]
    assert san_wheres and all(w == _SANITARY_WHERE for w in san_wheres)
    assert "STATUS_IND = 'ACTIVE'" in _SANITARY_WHERE and "'MAIN'" in _SANITARY_WHERE


# --- pagination -----------------------------------------------------------------

def test_pagination_concatenates_all_pages():
    client = FakeClient(page=15)         # 38 pipes -> pages of 15 (exceeded twice, then done)
    res = fetch_calgary_storm(BBOX, client=client)
    assert len(res["pipes"]) == len(PIPES)
    pipe_offsets = [int(p.get("resultOffset", 0) or 0) for (svc, p) in client.calls if svc == STORM_PIPES]
    assert pipe_offsets[0] == 0
    assert max(pipe_offsets) > 0, "later pages must advance resultOffset"
    assert len(pipe_offsets) >= 3, "expected >=3 pages for 38 features at page size 15"


# --- Esri-JSON fallback conversion ----------------------------------------------

class EsriJsonClient:
    """Serves the pipe fixture as raw Esri JSON (attributes + paths) instead of GeoJSON, to
    prove the adapter converts via base.esri_to_geojson when a layer doesn't honour f=geojson."""

    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        svc = _service(url)
        self.calls.append((svc, params))
        if svc == STORM_PIPES:
            esri = []
            for f in PIPES:
                coords = f["geometry"]["coordinates"]
                esri.append({"attributes": dict(f["properties"]),
                             "geometry": {"paths": [coords]}})
            return {"features": esri, "exceededTransferLimit": False}
        return {"type": "FeatureCollection", "features": []}


def test_esri_json_is_converted_to_geojson():
    res = fetch_calgary_storm(BBOX, client=EsriJsonClient())
    assert len(res["pipes"]) == len(PIPES)
    for f in res["pipes"]:
        assert f["type"] == "Feature"
        assert f["geometry"]["type"] in ("LineString", "MultiLineString")
        assert "coordinates" in f["geometry"]
        # attributes were lifted into GeoJSON properties
        assert "OBJECTID" in f["properties"]
        assert "UP_INVERT" in f["properties"]


# --- land fetch -----------------------------------------------------------------

def test_land_returns_catchbasins_parcels_buildings():
    res = fetch_calgary_land(BBOX, client=FakeClient())
    assert set(res) == {"catchbasins", "parcels", "buildings"}
    assert res["catchbasins"], "expected catch basins from fixture"
    for f in res["catchbasins"]:
        assert f["type"] == "Feature" and f["geometry"]["type"] == "Point"
        assert "ASSET_ID" in f["properties"]
    # parcels/buildings layers are queried (this fake serves them empty -> [])
    assert isinstance(res["parcels"], list)
    assert isinstance(res["buildings"], list)
    client = FakeClient()
    fetch_calgary_land(BBOX, client=client)
    queried = {svc for (svc, _) in client.calls}
    assert {STORM_CATCHBASINS, PARCELS, BUILDINGS} <= queried


def test_land_accepts_object_with_bbox_attribute():
    class AOI:
        bbox = BBOX

    res = fetch_calgary_land(AOI(), client=FakeClient())
    assert res["catchbasins"]


def test_outfall_where_excludes_unknown_stubs():
    """Audit 2026-07-14: OPEN ENDED STUBs carry OUT_INLET='UNKNOWN' and are dead pipe ends,
    not receiving waters — the where-clause must exclude them."""
    from swmmcanada.sources.cities.calgary import _OUTFALL_WHERE
    assert "OUT_INLET IS NOT NULL" in _OUTFALL_WHERE
    assert "UNKNOWN" in _OUTFALL_WHERE
