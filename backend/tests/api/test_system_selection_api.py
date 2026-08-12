"""System selection reaches the API (ADR 0029 Q3).

The frontend asks "which systems to include" rather than "storm or sanitary?", and it must
offer only the systems a city actually has. That list has one source of truth in the
backend, the same rule DATA_TIERS follows — a hardcoded frontend list drifts, exactly as
aiswmm's Regina-missing city list once did.
"""
import json

import pytest
from fastapi.testclient import TestClient

from swmmcanada.api import create_app
from swmmcanada.sources.cities.registry import CITIES, systems_for_city

OTTAWA = {"type": "Polygon", "coordinates": [[
    [-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]]}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(pipeline=lambda *a, **k: None, workdir=tmp_path,
                                 run_inline=True))


@pytest.fixture
def aoi_payload():
    return {"start_date": "2022-06-01", "end_date": "2022-06-07",
            "polygon": json.dumps(OTTAWA)}


class TestSystemsForCity:
    def test_every_registry_city_declares_its_systems(self):
        """Enforced like DATA_TIERS: a new city cannot be added without saying what it has."""
        missing = [c.key for c in CITIES if not systems_for_city(c.key)]
        assert not missing, f"cities with no declared systems: {missing}"

    def test_storm_is_always_present(self):
        """Storm is the product; everything else is additive."""
        for c in CITIES:
            assert "storm" in systems_for_city(c.key), c.key

    def test_a_city_publishing_a_sanitary_layer_declares_sanitary(self):
        assert "sanitary" in systems_for_city("victoria")

    def test_combined_is_declared_only_where_combined_mains_exist(self):
        """Measured, not assumed: Ottawa and Toronto serve combined mains, Victoria does
        not, and offering a dead checkbox is worse than offering none."""
        assert "combined" in systems_for_city("ottawa")
        assert "combined" not in systems_for_city("victoria")

    def test_an_unknown_city_has_no_systems_rather_than_guessing(self):
        assert systems_for_city("atlantis") == []


class TestCoverageEndpoint:
    def test_coverage_reports_each_city_systems(self, client):
        body = client.get("/api/v1/coverage").json()
        cities = body["real_network_cities"]
        entry = next(c for c in cities if c["key"] == "victoria")
        assert "systems" in entry and "storm" in entry["systems"]


class TestTaskAcceptsASelection:
    def test_a_selection_is_accepted(self, client, aoi_payload):
        r = client.post("/api/v1/tasks", data={**aoi_payload, "systems": "storm,sanitary"})
        assert r.status_code == 202, r.text

    def test_an_unknown_system_is_rejected_with_a_useful_message(self, client, aoi_payload):
        r = client.post("/api/v1/tasks", data={**aoi_payload, "systems": "storm,plumbing"})
        assert r.status_code == 422
        assert "plumbing" in r.text

    def test_omitting_the_selection_means_everything(self, client, aoi_payload):
        """A user who has not chosen expects the model they asked for, not a slice."""
        r = client.post("/api/v1/tasks", data=aoi_payload)
        assert r.status_code == 202, r.text

    def test_a_blank_selection_is_rejected(self, client, aoi_payload):
        """Whitespace rather than "" because HTTP clients drop empty form fields entirely —
        the field never reaches the server, so that case is indistinguishable from omitting
        it. A blank-but-present value is the reachable one, and it means the user chose
        nothing, which is not the same as choosing everything."""
        r = client.post("/api/v1/tasks", data={**aoi_payload, "systems": "   "})
        assert r.status_code == 422
        assert "Empty system selection" in r.text


class TestTheSelectionReachesTheBuild:
    """The API validated the selection and then handed it to the pipeline. The injected
    test pipeline swallows unknown keyword arguments, so a build entry point that does not
    accept `systems` would have failed only in production."""

    @pytest.mark.parametrize("entry", ["build_city", "build_from_aoi"])
    def test_both_build_entry_points_accept_a_selection(self, entry):
        import inspect

        from swmmcanada import pipeline

        sig = inspect.signature(getattr(pipeline, entry))
        assert "systems" in sig.parameters, f"{entry} cannot receive the API's selection"

    def test_the_selection_is_forwarded_verbatim(self, tmp_path):
        seen = {}

        def recording(aoi, start, end, ws, report=None, **kwargs):
            seen.update(kwargs)

        c = TestClient(create_app(pipeline=recording, workdir=tmp_path, run_inline=True))
        c.post("/api/v1/tasks", data={"start_date": "2022-06-01", "end_date": "2022-06-07",
                                      "polygon": json.dumps(OTTAWA),
                                      "systems": "storm, sanitary"})
        assert seen.get("systems") == ["storm", "sanitary"]
