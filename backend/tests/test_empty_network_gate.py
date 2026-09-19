"""The empty-network gate: coverage boxes are a GUESS about where a city's feed has data;
the feed is the evidence. Seen live: a Waterloo AOI dispatched to the Kitchener feed, which
holds no pipes there — the build ran to DONE, shipped a forcing-only .inp labelled "Real
municipal network", and validation said ok=True.

Contract under test: an adapter that returns no pipes never ships a model. The dispatcher
moves on to the next city whose box contains the AOI; when no feed has anything it
synthesizes, and the task's mode label and the package provenance say what really happened.
All offline — synthetic specs, the build functions stubbed at the dispatcher seam.
"""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from swmmcanada import pipeline
from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities import registry as reg

START, END = date(2023, 6, 1), date(2023, 6, 2)
AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-0.005, -0.005], [0.005, -0.005], [0.005, 0.005], [-0.005, 0.005], [-0.005, -0.005]]]})

EMPTY = NetworkIn(junctions=[], outfalls=[], conduits=[])
PIPED = NetworkIn(junctions=[JunctionIn("J1", 9.0, -0.001, 0.0)],
                  outfalls=[OutfallIn("OUT", 8.0, 0.001, 0.0)],
                  conduits=[ConduitIn("C1", "J1", "OUT", 120.0)])


def _spec(key, coverage, network):
    return reg.CitySpec(
        key=key, label=f"{key.title()}, XX", coverage=coverage, sub_crs="EPSG:32631",
        network_source=f"{key} feed",
        storm=lambda bbox, client: base.NetworkResult(network=network, diagnostics={}),
        land=lambda bbox, client: {})


INNER_EMPTY = _spec("inner", (-1.0, -1.0, 1.0, 1.0), EMPTY)
OUTER_PIPED = _spec("outer", (-10.0, -10.0, 10.0, 10.0), PIPED)
OUTER_EMPTY = _spec("outer", (-10.0, -10.0, 10.0, 10.0), EMPTY)


@pytest.fixture
def cities(monkeypatch):
    def _set(*specs):
        monkeypatch.setattr(reg, "CITIES", tuple(specs))
    return _set


def test_build_city_refuses_a_pipeless_network(tmp_path):
    """Raised right after the fetch — before DEM, climate or any other acquisition."""
    with pytest.raises(pipeline.EmptyCityNetwork) as exc:
        pipeline.build_city(INNER_EMPTY, AOI, START, END, tmp_path)
    assert "inner" in str(exc.value)
    assert not (tmp_path / "model.inp").exists()


def test_candidates_are_every_containing_box_smallest_first(cities):
    cities(OUTER_PIPED, INNER_EMPTY)              # outer listed first: order must NOT win
    assert [s.key for s in reg.cities_for_point(0.0, 0.0)] == ["inner", "outer"]
    assert [s.key for s in reg.cities_for_point(5.0, 5.0)] == ["outer"]
    assert reg.cities_for_point(50.0, 50.0) == []
    assert reg.city_for_point(0.0, 0.0).key == "inner"   # the single-answer form is unchanged


def test_dispatcher_binds_the_fallback_candidates(cities):
    cities(OUTER_PIPED, INNER_EMPTY)
    fn, mode = pipeline.pipeline_for_aoi(AOI)
    assert fn.args[0].key == "inner" and "Inner" in mode
    assert [s.key for s in fn.keywords["fallbacks"]] == ["outer"]


class TestFallThrough:
    @pytest.fixture
    def calls(self, monkeypatch):
        """Stub both build functions at the dispatcher seam; the real ``build_city`` gate is
        covered above, this is the orchestration around it."""
        log = {"city": [], "synth": []}

        def fake_city(spec, aoi, start, end, ws, **kw):
            log["city"].append((spec.key, kw))
            if not spec.storm(None, None).network.conduits:
                raise pipeline.EmptyCityNetwork(spec)
            return f"built:{spec.key}"

        def fake_synth(aoi, start, end, ws, **kw):
            log["synth"].append(kw)
            return "built:synthetic"

        monkeypatch.setattr(pipeline, "build_city", fake_city)
        monkeypatch.setattr(pipeline, "build_from_aoi", fake_synth)
        return log

    def test_a_city_with_pipes_builds_without_any_fallback(self, cities, calls, tmp_path):
        cities(OUTER_PIPED)
        modes = []
        fn, _ = pipeline.pipeline_for_aoi(AOI, on_mode=modes.append)
        assert fn(AOI, START, END, tmp_path) == "built:outer"
        assert modes == [] and calls["synth"] == []

    def test_an_empty_feed_falls_through_to_the_next_candidate(self, cities, calls, tmp_path):
        cities(INNER_EMPTY, OUTER_PIPED)
        modes = []
        fn, mode = pipeline.pipeline_for_aoi(AOI, on_mode=modes.append)
        assert "Inner" in mode                      # the up-front guess
        assert fn(AOI, START, END, tmp_path, infiltration="HORTON") == "built:outer"
        assert [k for k, _ in calls["city"]] == ["inner", "outer"]
        assert calls["city"][1][1]["infiltration"] == "HORTON"   # options survive the hop
        assert modes == ["Real municipal network: Outer, XX"]    # ...and the label follows
        assert calls["synth"] == []

    def test_no_feed_has_pipes_so_it_synthesizes_and_says_why(self, cities, calls, tmp_path):
        cities(INNER_EMPTY, OUTER_EMPTY)
        modes = []
        fn, _ = pipeline.pipeline_for_aoi(AOI, on_mode=modes.append)
        out = fn(AOI, START, END, tmp_path, infiltration="HORTON", client=object(),
                 subcatchment_method="parcel_row", subcatchment_layer=[{"x": 1}])
        assert out == "built:synthetic"
        assert modes[-1].startswith("Synthetic") and "no pipes" in modes[-1]
        kw = calls["synth"][0]
        assert kw["infiltration"] == "HORTON"
        # city-only options have no meaning on the synthesis pathway — dropped, but recorded
        assert not {"client", "subcatchment_method", "subcatchment_layer"} & set(kw)
        assert kw["real_network_fallback"] == {
            "tried": ["inner", "outer"],
            "reason": "the city feed(s) covering this AOI returned no pipes",
            "ignored_options": ["subcatchment_layer", "subcatchment_method"],
        }


def test_status_endpoint_reports_the_pathway_that_actually_ran(monkeypatch, tmp_path):
    """The mode label is decided before any fetch; when the build changes pathway the task
    must say so — the frontend badge polls this field."""
    import swmmcanada.api.app as app_mod
    from swmmcanada.api import create_app

    def fake_dispatch(aoi, on_mode=None):
        def build(aoi, start, end, ws, report=None, **kw):
            if on_mode:
                on_mode("Synthetic network from open data: corrected")
            raise RuntimeError("stop here — the label is what this test is about")
        return build, "Real municipal network: Guess, XX"

    monkeypatch.setattr(app_mod, "pipeline_for_aoi", fake_dispatch)
    client = TestClient(create_app(workdir=tmp_path, run_inline=True))
    ring = {"type": "Polygon", "coordinates": [[
        [-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]]}
    r = client.post("/api/v1/tasks", data={"start_date": "2022-06-01", "end_date": "2022-06-07",
                                           "polygon": json.dumps(ring)})
    assert r.status_code == 202, r.text
    assert r.json()["mode"] == "Real municipal network: Guess, XX"
    status = client.get(f"/api/v1/tasks/{r.json()['task_id']}").json()
    assert status["mode"] == "Synthetic network from open data: corrected"
