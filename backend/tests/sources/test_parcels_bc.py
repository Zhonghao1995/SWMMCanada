"""ParcelMap BC fetcher (F-025/ADR 0024): province pre-gate, sorted pagination, Road
filter, truncation and failure as distinct statuses."""
from swmmcanada.sources import parcels_bc
from swmmcanada.sources.parcels_bc import fetch_bc_parcels

BC_BBOX = (-123.516, 48.443, -123.500, 48.451)      # Langford
ON_BBOX = (-75.71, 45.41, -75.69, 45.43)            # Ottawa — not BC


def _feat(i, cls="Subdivision"):
    return {"type": "Feature",
            "properties": {"PARCEL_CLASS": cls, "PARCEL_FABRIC_POLY_ID": i},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, params):
        self.calls.append(params)
        start = int(params.get("startIndex", 0))
        page = int(params.get("count"))
        return {"features": self.pages[start: start + page]}


def test_outside_bc_short_circuits_without_network():
    client = FakeClient([])
    feats, status = fetch_bc_parcels(ON_BBOX, client=client)
    assert feats == [] and status["status"] == "not_applicable"
    assert client.calls == [], "no WFS round-trip for a non-BC AOI"


def test_road_class_filtered_and_pages_sorted(monkeypatch):
    monkeypatch.setattr(parcels_bc, "_PAGE", 3)
    rows = [_feat(1), _feat(2, "Road"), _feat(3), _feat(4), _feat(5, "Road"), _feat(6), _feat(7)]
    client = FakeClient(rows)
    feats, status = fetch_bc_parcels(BC_BBOX, client=client)
    assert status["status"] == "ok" and status["n"] == len(feats) == 5
    assert all(f["properties"]["PARCEL_CLASS"] != "Road" for f in feats)
    assert len(client.calls) == 3                       # 3+3+1 rows
    assert all(c.get("sortBy") == "PARCEL_FABRIC_POLY_ID" for c in client.calls)
    assert [int(c.get("startIndex", 0)) for c in client.calls] == [0, 3, 6]


def test_truncation_is_a_distinct_unusable_status(monkeypatch):
    monkeypatch.setattr(parcels_bc, "_PAGE", 3)
    monkeypatch.setattr(parcels_bc, "_MAX_PARCELS", 6)
    client = FakeClient([_feat(i) for i in range(9)])
    feats, status = fetch_bc_parcels(BC_BBOX, client=client)
    assert status["status"] == "truncated" and status["truncated"] is True
    assert feats == []                                  # half a cadastre is worse than none


def test_failure_is_graceful_and_labelled():
    def boom(url, params):
        raise RuntimeError("wfs down")

    feats, status = fetch_bc_parcels(BC_BBOX, client=boom)
    assert feats == [] and status["status"] == "failed"
