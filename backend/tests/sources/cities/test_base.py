"""Shared ArcGIS helpers in cities.base (ADR 0006 / multi-city Phase 0):
`esri_to_geojson` (Esri-JSON geometry -> GeoJSON Feature) and the thin `ArcGISClient`.
These are lifted out of the per-city adapters so every city reuses one copy.
"""
from swmmcanada.sources.cities.base import ArcGISClient, esri_to_geojson


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
    """get_json GETs with params+timeout, raises for status, returns parsed JSON."""
    import swmmcanada.sources.cities.base as base

    seen = {}

    class _Resp:
        def raise_for_status(self):
            seen["raised"] = True

        def json(self):
            return {"ok": True}

    def fake_get(url, params=None, timeout=None):
        seen.update(url=url, params=params, timeout=timeout)
        return _Resp()

    monkeypatch.setattr(base.requests, "get", fake_get)
    out = ArcGISClient(timeout=12.0).get_json("http://x/query", {"f": "json"})
    assert out == {"ok": True}
    assert seen == {"url": "http://x/query", "params": {"f": "json"}, "timeout": 12.0, "raised": True}
