"""The municipal reproduction mode: parcels as their own units + road right-of-way per node.

A municipal drawing keeps two kinds of storm unit apart — every lot is its own subcatchment,
and the street land forms one face per block — all discharging to maintenance holes
(composition follows municipal engineering records, 2026-08). Our existing vocabulary offers
either the node-aggregated unit (`junction_street_segment`) or lots dissolved per node
(`junction_parcel`); neither can be compared unit-for-unit against such a drawing.

`junction_parcel_row` is that composition assembled from existing seams: parcel shaping,
the junction-Voronoi split of the non-parcel remainder, `_outlet_resolver` frontage outlets
and the shared geometry discipline. It is REQUESTED, never chosen from evidence alone —
it multiplies model objects without adding information the node unit lacks, so the
evidence-driven defaults must not move.

Everything here is synthetic: a hand-built street band with two rows of lots. No municipal
data, no city branches.
"""
import pytest

from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn
from swmmcanada.delineation import Evidence, resolve
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.validate import schema

CRS = "EPSG:32610"

# One street running west-east at lat 48.4200, three maintenance holes, an outfall east of
# the extract. The road band is ~22 m wide; two rows of three lots front it.
AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.3720, 48.4190], [-123.3690, 48.4190], [-123.3690, 48.4210],
    [-123.3720, 48.4210], [-123.3720, 48.4190]]]})

NETWORK = NetworkIn(
    junctions=[JunctionIn("J1", invert_m=10.0, x=-123.3715, y=48.4200),
               JunctionIn("J2", invert_m=9.0, x=-123.3705, y=48.4200),
               JunctionIn("J3", invert_m=8.0, x=-123.3695, y=48.4200)],
    outfalls=[OutfallIn("O1", invert_m=7.0, x=-123.3689, y=48.4200)],
    conduits=[ConduitIn("C1", from_node="J1", to_node="J2", length_m=74, diameter_m=0.45),
              ConduitIn("C2", from_node="J2", to_node="J3", length_m=74, diameter_m=0.45),
              ConduitIn("C3", from_node="J3", to_node="O1", length_m=44, diameter_m=0.45)])

JUNCTION_XY = {j.name: (j.x, j.y) for j in NETWORK.junctions}


def _poly(ring):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _rect(lon0, lon1, lat0, lat1):
    return _poly([[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]])


# Two rows of lots, three per row, meeting the road band at 48.4199 / 48.4201. The column
# splits (-123.3710, -123.3700) coincide with the node midlines, so the expected frontage
# node per column is unambiguous.
PARCELS = [
    _rect(-123.3720, -123.3710, 48.4201, 48.4210),   # NW -> fronts J1's reach
    _rect(-123.3710, -123.3700, 48.4201, 48.4210),   # NM -> J2
    _rect(-123.3700, -123.3690, 48.4201, 48.4210),   # NE -> J3
    _rect(-123.3720, -123.3710, 48.4190, 48.4199),   # SW -> J1
    _rect(-123.3710, -123.3700, 48.4190, 48.4199),   # SM -> J2
    _rect(-123.3700, -123.3690, 48.4190, 48.4199),   # SE -> J3
]

NODE_NAMES = {"J1", "J2", "J3", "O1"}


def _delineate(parcels=None, buildings=None, laterals=None):
    from swmmcanada.sources.cities.base import delineate_parcel_row_subcatchments

    return delineate_parcel_row_subcatchments(
        NETWORK, JUNCTION_XY, PARCELS if parcels is None else parcels,
        buildings or [], AOI, crs=CRS, laterals=laterals)


def _expected_frontage_node(lon):
    if lon < -123.3710:
        return "J1"
    if lon < -123.3700:
        return "J2"
    return "J3"


class TestResolverVocabulary:
    """The mode enters the resolver's controlled vocabulary as a REQUEST (ADR 0029 Q11:
    the resolver stays the only place a method is chosen — the request is one more input,
    and whether the evidence can honour it is the resolver's call)."""

    EV = Evidence(n_junctions=3, n_parcels=6, n_streets=4)

    def test_the_request_is_honoured_when_nodes_and_parcels_exist(self):
        p = resolve(self.EV, requested_method=schema.METHOD_JUNCTION_PARCEL_ROW)
        assert p.method == schema.METHOD_JUNCTION_PARCEL_ROW
        assert (p.anchors, p.shaping) == ("junction", "parcel_row")
        assert p.gates.get("parcel_row_requested") is True
        assert p.gates.get("parcel_row_supported") is True

    def test_the_plan_explains_itself(self):
        p = resolve(self.EV, requested_method=schema.METHOD_JUNCTION_PARCEL_ROW)
        assert "6" in p.reason and "3" in p.reason        # quotes the numbers it used
        assert p.evidence and p.gates

    @pytest.mark.parametrize("ev", [
        Evidence(n_junctions=391, n_streets=250, n_parcels=3939),
        Evidence(n_junctions=391, n_parcels=3939),
        Evidence(n_junctions=391),
        Evidence(),
    ])
    def test_it_is_never_chosen_unrequested(self, ev):
        """Opt-in only: the evidence-driven defaults must not move (baseline protection)."""
        assert resolve(ev).method != schema.METHOD_JUNCTION_PARCEL_ROW

    def test_without_parcels_the_request_falls_back_and_says_so(self):
        """Buildings alone are land evidence for `junction_parcel`, but this mode's unit IS
        the parcel — no parcels, no parcel units, and no remainder either."""
        p = resolve(Evidence(n_junctions=391, n_buildings=5000),
                    requested_method=schema.METHOD_JUNCTION_PARCEL_ROW)
        assert p.method == schema.METHOD_JUNCTION_PARCEL
        assert p.gates.get("parcel_row_requested") is True
        assert p.gates.get("parcel_row_supported") is False
        assert "request" in p.reason.lower()

    def test_without_nodes_the_request_falls_back_to_the_floor(self):
        p = resolve(Evidence(n_parcels=50),
                    requested_method=schema.METHOD_JUNCTION_PARCEL_ROW)
        assert p.method == schema.METHOD_JUNCTION_VORONOI
        assert p.gates.get("parcel_row_supported") is False

    def test_a_user_layer_still_outranks_the_request(self):
        p = resolve(Evidence(n_user_units=12, n_junctions=3, n_parcels=6),
                    requested_method=schema.METHOD_JUNCTION_PARCEL_ROW)
        assert p.method == schema.METHOD_USER_SUPPLIED

    def test_an_official_basin_bounds_but_does_not_block_the_request(self):
        p = resolve(Evidence(n_junctions=3, n_parcels=6, official_basin_level="level_2"),
                    requested_method=schema.METHOD_JUNCTION_PARCEL_ROW)
        assert p.method == schema.METHOD_JUNCTION_PARCEL_ROW
        assert p.boundary == "official_basin"

    def test_the_city_field_changes_nothing(self):
        """The rule is generic by construction: two AOIs with identical evidence get the
        identical plan whatever city they sit in (no city/AOI branches — review item)."""
        a = resolve(Evidence(n_junctions=3, n_parcels=6, city="aaaa"),
                    requested_method=schema.METHOD_JUNCTION_PARCEL_ROW)
        b = resolve(Evidence(n_junctions=3, n_parcels=6, city="bbbb"),
                    requested_method=schema.METHOD_JUNCTION_PARCEL_ROW)
        assert a == b


class TestPipelineSeam:
    """`_plan_delineation` forwards the request to the resolver instead of deciding
    anything itself — data-availability branching stays out of the pipeline."""

    class FakeSpec:
        key = "faketown"

        def __init__(self, land):
            self._land = land
            self.calls = 0

        def land(self, bbox, client):
            self.calls += 1
            return self._land

    class FakeNetwork:
        junctions = [object()] * 7

    def test_the_request_reaches_the_resolver_with_the_land(self):
        from swmmcanada.pipeline import _plan_delineation

        spec = self.FakeSpec({"parcels": [1] * 40, "buildings": [1] * 10})
        land, plan = _plan_delineation(spec, (0, 0, 1, 1), None, self.FakeNetwork(),
                                       derive=True, subcatchment_method="parcel_row")
        assert spec.calls == 1 and len(land["parcels"]) == 40
        assert plan.method == schema.METHOD_JUNCTION_PARCEL_ROW

    def test_an_unmet_request_is_a_recorded_fallback_not_an_error(self):
        from swmmcanada.pipeline import _plan_delineation

        spec = self.FakeSpec({"buildings": [1] * 10})
        _, plan = _plan_delineation(spec, (0, 0, 1, 1), None, self.FakeNetwork(),
                                    derive=True, subcatchment_method="parcel_row")
        assert plan.method != schema.METHOD_JUNCTION_PARCEL_ROW
        assert plan.gates.get("parcel_row_requested") is True

    def test_the_voronoi_override_still_short_circuits(self):
        """The pre-existing override contract must not move."""
        from swmmcanada.pipeline import _plan_delineation

        spec = self.FakeSpec({"parcels": [1] * 40})
        land, plan = _plan_delineation(spec, (0, 0, 1, 1), None, self.FakeNetwork(),
                                       derive=True, subcatchment_method="voronoi")
        assert spec.calls == 0 and land == {}
        assert plan.gates == {"caller_override": True}


class TestPlanIsDelivered:
    """Mirrors test_every_plan_is_delivered: a plan that nothing executes is a silent
    downgrade to the nearest-node fallback while provenance records the plan."""

    def test_build_city_has_a_branch_for_the_shaping(self):
        import inspect

        from swmmcanada import pipeline

        plan = resolve(Evidence(n_junctions=3, n_parcels=6),
                       requested_method=schema.METHOD_JUNCTION_PARCEL_ROW)
        src = inspect.getsource(pipeline.build_city)
        branches = [line for line in src.splitlines() if "plan.shaping ==" in line]
        handled = {line.split("plan.shaping ==")[1].strip().rstrip(":").strip().strip('"')
                   for line in branches}
        assert plan.shaping in handled

    def test_the_method_descriptor_speaks_the_vocabulary(self):
        from swmmcanada.pipeline import _method_descriptor

        d = _method_descriptor({"method": schema.METHOD_JUNCTION_PARCEL_ROW})
        assert d.method == schema.METHOD_JUNCTION_PARCEL_ROW
        assert d.confidence == "medium" and d.physical_basis


class TestTwoUnitClasses:
    def test_units_come_in_two_classes_and_area_is_conserved(self):
        subs, imperv_map, diag = _delineate()
        parcel_units = [s for s in subs if s.name.startswith("S_P")]
        row_units = [s for s in subs if s.name.startswith("S_ROW_")]
        assert len(parcel_units) == 6
        assert len(row_units) >= 3                     # one per node takes the road band
        assert len(parcel_units) + len(row_units) == len(subs)
        assert diag["method"] == schema.METHOD_JUNCTION_PARCEL_ROW
        assert diag["n_parcel_units"] == 6
        assert diag["n_row_units"] == len(row_units)
        total_ha = sum(s.area_ha for s in subs)
        aoi_ha = AOI.area_km2 * 100.0
        assert total_ha == pytest.approx(aoi_ha, rel=0.01)

    def test_every_unit_has_an_outlet_in_the_network(self):
        subs, _, _ = _delineate()
        assert subs
        for s in subs:
            assert s.outlet_node in NODE_NAMES
            assert s.area_ha > 0
            assert s.width_m > 0

    def test_no_illegal_geometry_in_either_crs(self):
        """The repo rule (ADR 0023/0029): validity must hold in the stored CRS AND back in
        metric, because validation reprojects before checking."""
        from pyproj import Transformer
        from shapely.geometry import Polygon
        from shapely.ops import transform as shp_transform

        to_m = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform
        subs, _, _ = _delineate()
        for s in subs:
            assert s.polygon and len(s.polygon) >= 4
            ring = Polygon(s.polygon)
            assert ring.is_valid and not ring.is_empty
            ring_m = shp_transform(to_m, ring)
            assert ring_m.is_valid and not ring_m.is_empty


class TestOutletRules:
    def test_parcels_drain_to_the_pipe_of_the_street_they_front(self):
        """Generic rule, no city cases: the nearest conduit to a lot is the one in the
        street it faces, and the lot takes that pipe's nearer end (ADR 0032)."""
        from shapely.geometry import Polygon

        subs, _, _ = _delineate()
        parcel_units = [s for s in subs if s.name.startswith("S_P")]
        assert parcel_units
        for s in parcel_units:
            lon = Polygon(s.polygon).representative_point().x
            assert s.outlet_node == _expected_frontage_node(lon), s.name

    def test_row_units_drain_to_their_own_node(self):
        subs, _, _ = _delineate()
        row_units = [s for s in subs if s.name.startswith("S_ROW_")]
        assert row_units
        for s in row_units:
            node = s.name[len("S_ROW_"):].split("__")[0]
            assert s.outlet_node == node, s.name

    def test_a_lateral_redirects_a_parcel_to_its_stated_main(self):
        """Laterals stay the strongest connection evidence (ADR 0032): where one says which
        main a lot taps, the frontage guess yields to it — through the existing resolver
        seam, not new code."""
        laterals = [{"geometry": {"type": "LineString",
                                  "coordinates": [[-123.3715, 48.42055],
                                                  [-123.3695, 48.4200]]}}]
        subs, _, _ = _delineate(laterals=laterals)
        from shapely.geometry import Polygon

        nw = next(s for s in subs if s.name.startswith("S_P")
                  and Polygon(s.polygon).representative_point().x < -123.3710
                  and Polygon(s.polygon).representative_point().y > 48.4200)
        assert nw.outlet_node == "J3"


class TestDonutLesson:
    def test_a_remainder_donut_is_not_a_unit_and_its_land_survives(self):
        """The road right-of-way published AS a giant holed 'parcel' (the Moncton failure)
        must be kicked back to the remainder path, not become a unit that blankets the
        lots inside it."""
        donut = _poly(
            [[-123.3720, 48.4190], [-123.3690, 48.4190], [-123.3690, 48.4210],
             [-123.3720, 48.4210], [-123.3720, 48.4190]]
        )
        donut["geometry"]["coordinates"].extend(
            f["geometry"]["coordinates"][0] for f in PARCELS)
        subs, _, diag = _delineate(parcels=PARCELS + [donut])
        assert diag["n_parcels_dropped_remainder"] == 1
        assert diag["n_parcel_units"] == 6              # the real lots, not the donut
        total_ha = sum(s.area_ha for s in subs)
        assert total_ha == pytest.approx(AOI.area_km2 * 100.0, rel=0.01)

    def test_no_parcels_returns_empty_for_the_fallback_chain(self):
        subs, imperv_map, diag = _delineate(parcels=[])
        assert subs == [] and imperv_map == {}
        assert diag.get("reason")


class TestValidationRunsAsUsual:
    def test_the_standard_checks_run_and_pass_on_the_synthetic_fixture(self):
        from swmmcanada.pipeline import _method_descriptor
        from swmmcanada.validate import validate_model

        subs, _, diag = _delineate()
        report = validate_model(NETWORK, subs, AOI, method=_method_descriptor(diag))
        ids = {c.id for c in report.checks}
        assert {"outlet_present", "outlet_exists", "area_positive", "geometry_valid",
                "overlap", "area_conservation", "aoi_coverage"} <= ids
        assert report.ok, [c.message for c in report.errors]
        failing = [c.id for c in report.checks if not c.passed]
        assert failing == [], failing

    def test_the_noise_share_is_reported_not_hidden(self):
        """Municipal-scale lots are legitimately small; the mode reports its noise-scale
        share instead of gating on it — a number in the report, never a silent verdict."""
        _, _, diag = _delineate()
        assert "noise_cell_share" in diag
