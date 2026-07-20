"""Shared ArcGIS helpers in cities.base (ADR 0006 / multi-city Phase 0):
`esri_to_geojson` (Esri-JSON geometry -> GeoJSON Feature) and the thin `ArcGISClient`.
These are lifted out of the per-city adapters so every city reuses one copy.
"""
from swmmcanada.sources.cities.base import ArcGISClient, esri_to_geojson, fetch_paged


def test_esri_single_path_to_linestring():
    feat = {"attributes": {"ID": 1}, "geometry": {"paths": [[[0, 0], [1, 1]]]}}
    gj = esri_to_geojson(feat)
    assert gj["type"] == "Feature"
    assert gj["properties"] == {"ID": 1}
    assert gj["geometry"] == {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}


def test_esri_multi_path_to_multilinestring():
    feat = {"attributes": {}, "geometry": {"paths": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]}}
    gj = esri_to_geojson(feat)
    assert gj["geometry"]["type"] == "MultiLineString"
    assert gj["geometry"]["coordinates"] == [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]


def test_esri_point_to_point():
    feat = {"attributes": {}, "geometry": {"x": -75.7, "y": 45.4}}
    assert esri_to_geojson(feat)["geometry"] == {"type": "Point", "coordinates": [-75.7, 45.4]}


def test_esri_rings_to_polygon():
    rings = [[[0, 0], [1, 0], [1, 1], [0, 0]]]
    assert esri_to_geojson({"attributes": {}, "geometry": {"rings": rings}})["geometry"] == {
        "type": "Polygon", "coordinates": rings
    }


def test_esri_empty_geometry_to_none():
    gj = esri_to_geojson({"attributes": {"ID": 9}, "geometry": {}})
    assert gj["properties"] == {"ID": 9}
    assert gj["geometry"] is None


def test_arcgis_client_get_json(monkeypatch):
    """get_json requests GET with params+timeout, raises for status, returns parsed JSON —
    now through the shared retry helper (base.ArcGISClient no longer touches requests directly)."""
    from swmmcanada.sources import _http

    seen = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            seen["raised"] = True

        def json(self):
            return {"ok": True}

    def fake_request(method, url, params=None, timeout=None, **kw):
        seen.update(method=method, url=url, params=params, timeout=timeout)
        return _Resp()

    monkeypatch.setattr(_http.requests, "request", fake_request)
    out = ArcGISClient(timeout=12.0).get_json("http://x/query", {"f": "json"})
    assert out == {"ok": True}
    assert seen == {"method": "GET", "url": "http://x/query",
                    "params": {"f": "json"}, "timeout": 12.0, "raised": True}


class _PagingClient:
    """Serves ``total`` synthetic features page by page, reporting ``exceededTransferLimit``
    where the given server style puts it: ``top`` = top-level (Esri JSON everywhere; GeoJSON on
    self-hosted ArcGIS Server — Victoria/Kelowna/Regina...), ``nested`` = under the GeoJSON
    collection's ``properties`` (AGOL / newer hosted FeatureServers — Calgary/Kitchener/Vancouver)."""

    def __init__(self, total, style):
        self.total, self.style = total, style
        self.offsets = []

    def get_json(self, url, params):
        offset = int(params["resultOffset"])
        n = int(params["resultRecordCount"])
        self.offsets.append(offset)
        page = [{"type": "Feature", "properties": {"OBJECTID": i}, "geometry": None}
                for i in range(offset, min(offset + n, self.total))]
        payload = {"features": page}
        if offset + n < self.total:
            if self.style == "top":
                payload["exceededTransferLimit"] = True
            else:
                payload["properties"] = {"exceededTransferLimit": True}
        return payload


def test_fetch_paged_drains_nested_geojson_flag():
    """AGOL hosted FeatureServers nest ``exceededTransferLimit`` under the GeoJSON collection's
    ``properties`` — the loop must read it there too, or any AOI beyond one page silently
    truncates (live Calgary: 4716-pipe AOI returned exactly 2000; surfaced by PR #153)."""
    client = _PagingClient(total=25, style="nested")
    feats = fetch_paged(client, "http://x/query", (0, 0, 1, 1), page_size=10)
    assert len(feats) == 25
    assert client.offsets == [0, 10, 20]


def test_fetch_paged_drains_toplevel_flag():
    """The pre-existing contract: a top-level flag (Esri JSON, ArcGIS Server GeoJSON) pages fully."""
    client = _PagingClient(total=25, style="top")
    feats = fetch_paged(client, "http://x/query", (0, 0, 1, 1), page_size=10)
    assert len(feats) == 25
    assert client.offsets == [0, 10, 20]


def test_fetch_paged_single_page_stops_immediately():
    """No flag anywhere (result fits one page) -> exactly one request, no phantom second page."""
    client = _PagingClient(total=7, style="nested")
    feats = fetch_paged(client, "http://x/query", (0, 0, 1, 1), page_size=10)
    assert len(feats) == 7
    assert client.offsets == [0]
