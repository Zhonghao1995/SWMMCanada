"""Uploading your own subcatchment layer (resolver priority 0).

The backend has accepted a user layer since the resolver learned to prefer it. Nobody could
send one, which is the same defect as a branch nothing executes — the capability existed and
was unreachable.

The layer is a set of polygons, not an area of interest, so it takes its own field. Mixing
it into the AOI upload would make "the boundary I am modelling" and "how I want it divided"
the same input.
"""
import json

import pytest
from fastapi.testclient import TestClient

from swmmcanada.api import create_app

OTTAWA = {"type": "Polygon", "coordinates": [[
    [-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]]}

LAYER = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"name": "A"},
     "geometry": {"type": "Polygon", "coordinates": [[
         [-75.700, 45.410], [-75.690, 45.410], [-75.690, 45.415], [-75.700, 45.415],
         [-75.700, 45.410]]]}},
    {"type": "Feature", "properties": {"name": "B"},
     "geometry": {"type": "Polygon", "coordinates": [[
         [-75.690, 45.410], [-75.680, 45.410], [-75.680, 45.415], [-75.690, 45.415],
         [-75.690, 45.410]]]}},
]}


@pytest.fixture
def seen():
    return {}


@pytest.fixture
def client(tmp_path, seen):
    def recording(aoi, start, end, ws, report=None, **kwargs):
        seen.update(kwargs)

    return TestClient(create_app(pipeline=recording, workdir=tmp_path, run_inline=True))


def _payload(**extra):
    return {"start_date": "2022-06-01", "end_date": "2022-06-07",
            "polygon": json.dumps(OTTAWA), **extra}


class TestItReachesTheBuild:
    def test_an_uploaded_layer_is_forwarded(self, client, seen):
        r = client.post("/api/v1/tasks", data=_payload(
            subcatchment_layer=json.dumps(LAYER)))
        assert r.status_code == 202, r.text
        assert len(seen["subcatchment_layer"]) == 2

    def test_a_bare_feature_list_is_accepted_too(self, client, seen):
        """Not everyone exports a FeatureCollection."""
        client.post("/api/v1/tasks",
                    data=_payload(subcatchment_layer=json.dumps(LAYER["features"])))
        assert len(seen["subcatchment_layer"]) == 2

    def test_omitting_it_means_we_decide(self, client, seen):
        client.post("/api/v1/tasks", data=_payload())
        assert seen.get("subcatchment_layer") is None


class TestItRefusesWhatItCannotUse:
    def test_unparseable_text_is_rejected_with_a_useful_message(self, client):
        r = client.post("/api/v1/tasks", data=_payload(subcatchment_layer="{not json"))
        assert r.status_code == 422
        assert "subcatchment layer" in r.text.lower()

    def test_a_layer_with_no_polygons_is_rejected(self, client):
        """A file of points is a mistake worth catching at the door rather than three
        minutes into a build."""
        points = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [-75.69, 45.41]}}]}
        r = client.post("/api/v1/tasks", data=_payload(
            subcatchment_layer=json.dumps(points)))
        assert r.status_code == 422
        assert "polygon" in r.text.lower()

    def test_an_empty_collection_is_rejected(self, client):
        r = client.post("/api/v1/tasks", data=_payload(
            subcatchment_layer=json.dumps({"type": "FeatureCollection", "features": []})))
        assert r.status_code == 422
