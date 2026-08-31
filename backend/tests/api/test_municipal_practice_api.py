"""Municipal practice reaches the API (spec §G2 consumers 2 and 3).

Two thin channels, no new endpoints: the AOI preview — the existing draw-time site-facts
channel (city, tier, systems) — gains the one-line practice note, and the task form gains
the "follow municipal practice" option stub. The option is validated and forwarded like
`infiltration`/`systems`; what it eventually changes lands in later tickets, so here it
only has to exist and be recorded.
"""
import json

import pytest
from fastapi.testclient import TestClient

from swmmcanada.api import create_app
from swmmcanada.sources.cities.practice import (
    MUNICIPAL_PRACTICE, MunicipalPractice, PracticeItem,
)

OTTAWA = {"type": "Polygon", "coordinates": [[
    [-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]]}
RURAL_SK = {"type": "Polygon", "coordinates": [[
    [-106.10, 52.10], [-106.08, 52.10], [-106.08, 52.12], [-106.10, 52.12], [-106.10, 52.10]]]}

FAKE_SOURCE = "example municipal correspondence, 2026-01"


def _entry():
    return MunicipalPractice(
        infiltration_method=PracticeItem("GREEN_AMPT", FAKE_SOURCE, "2026-01"))


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(pipeline=lambda *a, **k: None, workdir=tmp_path,
                                 run_inline=True))


@pytest.fixture
def aoi_payload():
    return {"start_date": "2022-06-01", "end_date": "2022-06-07",
            "polygon": json.dumps(OTTAWA)}


class TestPreviewCarriesTheNote:
    def test_a_city_with_no_record_gets_null_not_a_default(self, client, monkeypatch):
        monkeypatch.delitem(MUNICIPAL_PRACTICE, "ottawa", raising=False)
        body = client.post("/api/v1/aoi/preview",
                           data={"polygon": json.dumps(OTTAWA)}).json()
        assert body["city"] == "ottawa"
        assert body["municipal_practice_note"] is None

    def test_a_registered_city_gets_the_one_line_note(self, client, monkeypatch):
        monkeypatch.setitem(MUNICIPAL_PRACTICE, "ottawa", _entry())
        body = client.post("/api/v1/aoi/preview",
                           data={"polygon": json.dumps(OTTAWA)}).json()
        assert isinstance(body["municipal_practice_note"], str)
        assert "Green-Ampt" in body["municipal_practice_note"]

    def test_a_synthesis_aoi_has_no_note(self, client):
        body = client.post("/api/v1/aoi/preview",
                           data={"polygon": json.dumps(RURAL_SK)}).json()
        assert body["city"] is None
        assert body["municipal_practice_note"] is None


class TestTaskAcceptsTheOption:
    def test_selecting_follow_is_accepted(self, client, aoi_payload):
        r = client.post("/api/v1/tasks",
                        data={**aoi_payload, "follow_municipal_practice": "true"})
        assert r.status_code == 202, r.text

    def test_omitting_it_is_the_default(self, client, aoi_payload):
        r = client.post("/api/v1/tasks", data=aoi_payload)
        assert r.status_code == 202, r.text

    def test_a_non_boolean_value_is_rejected(self, client, aoi_payload):
        r = client.post("/api/v1/tasks",
                        data={**aoi_payload, "follow_municipal_practice": "maybe"})
        assert r.status_code == 422


class TestTheOptionReachesTheBuild:
    """Same contract as the system selection: the injected test pipeline swallows unknown
    kwargs, so an entry point that cannot receive the option would fail only in
    production."""

    @pytest.mark.parametrize("entry", ["build_city", "build_from_aoi"])
    def test_both_build_entry_points_accept_the_option(self, entry):
        import inspect

        from swmmcanada import pipeline

        sig = inspect.signature(getattr(pipeline, entry))
        assert "follow_municipal_practice" in sig.parameters, entry

    def test_selecting_it_is_forwarded(self, tmp_path):
        seen = {}

        def recording(aoi, start, end, ws, report=None, **kwargs):
            seen.update(kwargs)

        c = TestClient(create_app(pipeline=recording, workdir=tmp_path, run_inline=True))
        c.post("/api/v1/tasks", data={"start_date": "2022-06-01", "end_date": "2022-06-07",
                                      "polygon": json.dumps(OTTAWA),
                                      "follow_municipal_practice": "true"})
        assert seen.get("follow_municipal_practice") is True

    def test_not_selecting_it_binds_nothing(self, tmp_path):
        """Bind only when asked (the infiltration/design-storm precedent), so injected
        test pipelines keep seeing exactly the calls they always did."""
        seen = {}

        def recording(aoi, start, end, ws, report=None, **kwargs):
            seen.update(kwargs)

        c = TestClient(create_app(pipeline=recording, workdir=tmp_path, run_inline=True))
        c.post("/api/v1/tasks", data={"start_date": "2022-06-01", "end_date": "2022-06-07",
                                      "polygon": json.dumps(OTTAWA)})
        assert "follow_municipal_practice" not in seen
