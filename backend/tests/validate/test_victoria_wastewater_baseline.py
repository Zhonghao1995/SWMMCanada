"""Victoria wastewater baseline — the reference city for the three-system work.

ADR 0029 Q8: prove the new types, resolver, validation and loading on one city before
touching the fleet, and lock the result so a later change is a visible diff rather than a
silent drift. Victoria is the reference because Phase 0 measured it as the best-equipped
city in the fleet for inference — two sets of laterals, kerbs, parcels and buildings — and
because its published "Sewer SubCatchment Areas" turned out to be a 57-polygon pump-station
basin, which is why none of this reads official polygons directly.

Its sanitary fixture turned out to teach a different lesson than expected. Victoria
publishes **zero** sanitary outfalls — but the model still validates, because the assembler
has always promoted each stranded component's lowest node into one. So the system does have
a destination; it is just that all 19 of them are invented. That is a subtler failure than a
missing outfall, which fails loudly: an invented one that looks published passes quietly and
is then used. Hence the marker these tests lock.
"""
import json
from pathlib import Path

import pytest

from swmmcanada.build.models import SewerServiceArea, filter_system, filter_system_report
from swmmcanada.delineation.outlet import ensure_wastewater_outlet
from swmmcanada.delineation.service_area import derive_service_areas
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.loading import load_service_areas
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.victoria import build_victoria_network
from swmmcanada.validate.checks import check_system_outfalls

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "victoria"

# --- locked baseline: today's behaviour; a legitimate change is a one-line diff ---
VIC_SAN_MAINS_IN = 528          # published mains in the fixture
VIC_SAN_CONDUITS = 547          # + 19 links to the invented outfalls
VIC_SAN_JUNCTIONS = 541
VIC_SAN_PUBLISHED_OUTFALLS = 0  # the city publishes none
VIC_SAN_COMPONENTS = 19         # -> 19 invented boundaries, one per stranded component


def _load(name):
    d = json.loads((FIXTURES / f"{name}.geojson").read_text())
    return d["features"] if isinstance(d, dict) else d


@pytest.fixture(scope="module")
def sanitary():
    return build_victoria_network(
        mains=_load("sanitary_mains"), manholes=_load("sanitary_manholes"),
        fittings=_load("sanitary_fittings"), outfalls=_load("sanitary_outfalls"))


@pytest.fixture(scope="module")
def storm():
    return build_victoria_network(
        mains=_load("mains"), manholes=_load("manholes"),
        fittings=_load("fittings"), outfalls=_load("outfalls"))


@pytest.fixture(scope="module")
def merged(storm, sanitary):
    """What build_city produces: storm, then the sanitary subgraph grafted behind SAN_."""
    return base.merge_secondary_system(storm.network, sanitary.network,
                                       prefix="SAN_", system="sanitary")


@pytest.fixture(scope="module")
def aoi(storm):
    xs = [j.x for j in storm.network.junctions]
    ys = [j.y for j in storm.network.junctions]
    pad = 0.002
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [min(xs) - pad, min(ys) - pad], [max(xs) + pad, min(ys) - pad],
        [max(xs) + pad, max(ys) + pad], [min(xs) - pad, max(ys) + pad],
        [min(xs) - pad, min(ys) - pad]]]})


class TestEveryDestinationInThisSystemIsInvented:
    def test_shape_of_the_built_sanitary_network(self, sanitary):
        assert sanitary.diagnostics["n_mains_in"] == VIC_SAN_MAINS_IN
        assert len(sanitary.network.conduits) == VIC_SAN_CONDUITS
        assert len(sanitary.network.junctions) == VIC_SAN_JUNCTIONS

    def test_the_city_publishes_no_sanitary_outfall(self, sanitary):
        assert sanitary.diagnostics["n_direct_outfalls"] == VIC_SAN_PUBLISHED_OUTFALLS

    def test_one_invented_boundary_per_stranded_component(self, sanitary):
        assert sanitary.diagnostics["n_components"] == VIC_SAN_COMPONENTS
        assert len(sanitary.network.outfalls) == VIC_SAN_COMPONENTS

    def test_all_of_them_are_marked_as_boundaries(self, sanitary):
        """The number that matters for reading a result: 19 of 19."""
        assert all(o.synthesised for o in sanitary.network.outfalls)

    def test_the_merged_model_therefore_validates(self, merged):
        """It always did — which is why the marker, not the check, was the gap."""
        assert check_system_outfalls(merged).passed


class TestTerminalOutletIsANoOpHere:
    def test_nothing_is_added_because_the_assembler_already_did_it(self, merged):
        """ensure_wastewater_outlet covers networks that arrive without a destination. The
        city path is not one of them, and it must not pile a second boundary on top."""
        _, diag = ensure_wastewater_outlet(merged, system="sanitary")
        assert diag["n_added"] == 0

    def test_the_storm_outfalls_are_untouched(self, merged, storm):
        fixed, _ = ensure_wastewater_outlet(merged, system="sanitary")
        storm_out = [o.name for o in fixed.outfalls if o.system == "storm_minor"]
        assert len(storm_out) == len(storm.network.outfalls)


class TestServiceAreasAndLoading:
    @pytest.fixture(scope="class")
    def loaded(self, merged, aoi):
        fixed, _ = ensure_wastewater_outlet(merged, system="sanitary")
        areas, diag = derive_service_areas(
            filter_system(fixed, "sanitary"), parcels=[], aoi=aoi, crs="EPSG:32610")
        return load_service_areas(areas), diag

    def test_every_manhole_serves_an_area(self, loaded):
        res, diag = loaded
        assert diag["seed_source"] == "manhole", "no laterals in this fixture"
        assert len(res.areas) > 0

    def test_areas_are_service_areas_not_subcatchments(self, loaded):
        res, _ = loaded
        assert all(isinstance(a, SewerServiceArea) for a in res.areas)

    def test_every_area_loads_a_sanitary_node(self, loaded, merged):
        res, _ = loaded
        fixed, _d = ensure_wastewater_outlet(merged, system="sanitary")
        sanitary_names = {n.name for n in list(fixed.junctions) + list(fixed.outfalls)
                          if n.system == "sanitary"}
        assert all(a.node in sanitary_names for a in res.areas)

    def test_the_loading_is_honest_about_resting_on_assumed_density(self, loaded):
        """No parcels or address points in this fixture, so every area is on the floor rung
        — and the build says so rather than letting a precise flow imply precise evidence."""
        res, _ = loaded
        assert res.diagnostics["pct_on_assumed_density"] == 100.0
        assert res.diagnostics["coefficient_source"] == "synthetic"

    def test_total_flow_is_physically_plausible(self, loaded):
        """A sanity band, not a baseline: downtown Victoria's sanitary fixture should carry
        litres per second, not millilitres and not cubic metres."""
        res, _ = loaded
        assert 1.0 < res.diagnostics["total_dwf_lps"] < 500.0


class TestSystemViews:
    def test_the_sanitary_view_stands_alone(self, merged):
        fixed, _ = ensure_wastewater_outlet(merged, system="sanitary")
        view, report = filter_system_report(fixed, ["sanitary"])
        assert report["n_orphaned_nodes"] == 0, report["note"]
        assert len(view.junctions) == VIC_SAN_JUNCTIONS

    def test_the_storm_view_stands_alone(self, merged):
        fixed, _ = ensure_wastewater_outlet(merged, system="sanitary")
        _, report = filter_system_report(fixed, ["storm_minor"])
        assert report["n_orphaned_nodes"] == 0, report["note"]

    def test_victoria_has_no_combined_system_to_view(self):
        """Two CWW relics its adapter excludes; a checkbox for two pipes would misdescribe
        the city."""
        from swmmcanada.sources.cities.registry import systems_for_city

        assert systems_for_city("victoria") == ["storm", "sanitary"]
