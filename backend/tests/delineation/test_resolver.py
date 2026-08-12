"""The resolver is the sole method-selection entry point (ADR 0029 Q10/Q11)."""
import pytest

from swmmcanada.delineation import DelineationPlan, Evidence, resolve
from swmmcanada.validate import schema


class TestChoices:
    """The unit never changes — land is divided among the model's NODES — and the methods
    differ only in how it is divided. A subcatchment has to discharge to a node that exists,
    and the reach between two nodes has one tributary area however many inlets sit on it."""

    def test_streets_give_the_municipal_unit(self):
        p = resolve(Evidence(n_junctions=391, n_streets=250, n_parcels=15728))
        assert (p.method, p.anchors, p.shaping) == (
            schema.METHOD_JUNCTION_STREET, "junction", "street_segment")

    def test_a_fine_surface_without_streets_follows_terrain(self):
        p = resolve(Evidence(n_junctions=400, dem_available=True, dem_resolution_m=1.0))
        assert (p.method, p.shaping) == (schema.METHOD_JUNCTION_DEM, "dem_d8")

    def test_parcels_alone_still_beat_a_bisector(self):
        p = resolve(Evidence(n_junctions=391, n_parcels=15728, n_buildings=21969))
        assert (p.method, p.shaping) == (schema.METHOD_JUNCTION_PARCEL, "parcel")

    def test_nothing_but_nodes_is_the_honest_floor(self):
        p = resolve(Evidence(n_junctions=400))
        assert p.method == schema.METHOD_JUNCTION_VORONOI and p.confidence == "low"

    def test_buildings_alone_count_as_land(self):
        """Ottawa publishes buildings but no parcels; that is still real land evidence."""
        p = resolve(Evidence(n_junctions=391, n_buildings=5000))
        assert p.method == schema.METHOD_JUNCTION_PARCEL

    def test_inlets_are_evidence_not_a_unit(self):
        """Catch basins tell us which main a lead taps. They do not become the thing land
        is divided among — that would put a surface structure in the hydraulic model."""
        p = resolve(Evidence(n_junctions=391, n_catchbasins=7864, n_parcels=15728))
        assert p.anchors == "junction"

    def test_resolve_is_total(self):
        assert resolve(Evidence()).method == schema.METHOD_JUNCTION_VORONOI


class TestEvidenceIsRuntimeNotCityLevel:
    def test_a_city_with_data_but_none_in_this_aoi_falls_through(self):
        """Victoria publishes 3,939 parcels and a lakeside AOI can contain none. A static
        capability row would claim they are available and be wrong here."""
        p = resolve(Evidence(n_parcels=0, n_junctions=40, city="victoria"))
        assert p.method == schema.METHOD_JUNCTION_VORONOI


class TestOfficialBasinsBoundOnly:
    def test_an_official_layer_bounds_but_never_selects(self):
        """Phase 0: every official polygon layer measured across 36 cities is a macro
        basin. It constrains where units may go; it does not become the units."""
        with_basin = resolve(Evidence(n_junctions=100, n_parcels=100,
                                      official_basin_level="level_2"))
        without = resolve(Evidence(n_junctions=100, n_parcels=100))
        assert with_basin.boundary == "official_basin"
        assert without.boundary == "aoi"
        assert with_basin.method == without.method

    def test_review_required_does_not_bound(self):
        p = resolve(Evidence(n_junctions=100, n_parcels=100,
                             official_basin_level="level_2_review_required"))
        assert p.boundary == "aoi"


class TestPlansExplainThemselves:
    """ADR 0029 Q11: never a bare label."""

    @pytest.mark.parametrize("ev", [
        Evidence(n_junctions=100, n_streets=80, n_parcels=100),
        Evidence(n_junctions=100, n_parcels=100),
        Evidence(n_junctions=10, dem_available=True, dem_resolution_m=1.0),
        Evidence(),
    ])
    def test_every_plan_carries_reason_gates_and_evidence(self, ev):
        p = resolve(ev)
        assert p.reason and p.gates and isinstance(p.evidence, dict)
        assert {"nodes_present", "streets_present", "land_present", "dem_present",
                "kerb_usable", "terrain_usable"} <= set(p.gates)

    def test_reason_quotes_the_numbers_it_used(self):
        p = resolve(Evidence(n_junctions=391, n_parcels=15728, n_buildings=21969))
        assert "391" in p.reason and "15728" in p.reason

    def test_voronoi_never_claims_to_be_a_delineation(self):
        r = resolve(Evidence(n_junctions=10)).reason.lower()
        assert "geometric" in r and "not hydrological" in r

    def test_plan_serialises_for_provenance(self):
        d = resolve(Evidence(n_junctions=5, n_parcels=5)).as_dict()
        assert set(d) >= {"method", "boundary", "anchors", "shaping", "reason",
                          "gates", "evidence", "confidence"}


class TestPipelineSeam:
    """`_plan_delineation` is where the pipeline hands the decision over. If it ever
    branches on data itself, the recorded reason stops being the real one."""

    class FakeSpec:
        key = "faketown"

        def __init__(self, land):
            self._land = land
            self.calls = 0

        def land(self, bbox, client):
            self.calls += 1
            return self._land

    class FakeNetwork:
        junctions = [object()] * 42

    def test_seam_uses_the_resolver_and_returns_the_land_it_fetched(self):
        from swmmcanada.pipeline import _plan_delineation
        spec = self.FakeSpec({"catchbasins": [1] * 300, "parcels": [1] * 50,
                              "buildings": [1] * 80})
        land, plan = _plan_delineation(spec, (0, 0, 1, 1), None, self.FakeNetwork(),
                                       derive=True, subcatchment_method="parcel")
        assert plan.method == schema.METHOD_JUNCTION_PARCEL
        assert plan.evidence["n_catchbasins"] == 300
        assert len(land["parcels"]) == 50 and spec.calls == 1

    def test_caller_override_is_recorded_and_skips_the_land_fetch(self):
        """The override is a decision too, so it is recorded in the same shape — and it
        must not pay for evidence that cannot change the outcome."""
        from swmmcanada.pipeline import _plan_delineation
        spec = self.FakeSpec({"catchbasins": [1] * 300})
        land, plan = _plan_delineation(spec, (0, 0, 1, 1), None, self.FakeNetwork(),
                                       derive=True, subcatchment_method="voronoi")
        assert spec.calls == 0, "land must not be fetched when the plan is already fixed"
        assert land == {} and plan.anchors == "junction"
        assert "override" in plan.reason and plan.gates == {"caller_override": True}

    def test_seam_counts_junctions_from_the_network(self):
        from swmmcanada.pipeline import _plan_delineation
        spec = self.FakeSpec({})
        _, plan = _plan_delineation(spec, (0, 0, 1, 1), None, self.FakeNetwork(),
                                    derive=False, subcatchment_method="parcel")
        assert plan.evidence["n_junctions"] == 42
        assert plan.method == schema.METHOD_JUNCTION_VORONOI


class TestTerrainAndKerbs:
    """规划书 §4 priorities 2-3, on the node unit. Kerbs are one more input on the same
    pipeline: they change what the answer is worth, not how it is produced."""

    def test_a_fine_surface_routes_land_over_terrain(self):
        p = resolve(Evidence(n_junctions=391, n_parcels=3939, dem_available=True,
                             dem_resolution_m=1.0))
        assert (p.method, p.shaping) == (schema.METHOD_JUNCTION_DEM, "dem_d8")

    def test_kerbs_raise_the_confidence_without_changing_the_method(self):
        plain = resolve(Evidence(n_junctions=391, dem_available=True, dem_resolution_m=1.0))
        kerbed = resolve(Evidence(n_junctions=391, n_kerbs=2189, dem_available=True,
                                  dem_resolution_m=1.0))
        assert plain.method == kerbed.method == schema.METHOD_JUNCTION_DEM
        assert (plain.confidence, kerbed.confidence) == ("medium", "high")

    def test_a_coarse_surface_is_not_a_usable_one(self):
        """A 30 m posting cannot resolve a city block, and a 150 mm kerb is far below its
        vertical noise."""
        p = resolve(Evidence(n_junctions=391, n_parcels=3939, n_kerbs=2189,
                             dem_available=True, dem_resolution_m=30.0))
        assert p.method == schema.METHOD_JUNCTION_PARCEL
        assert p.gates["kerb_usable"] is False

    def test_no_surface_at_all_falls_to_lot_lines(self):
        p = resolve(Evidence(n_junctions=391, n_parcels=3939, dem_available=False))
        assert p.method == schema.METHOD_JUNCTION_PARCEL

    def test_streets_outrank_terrain(self):
        """A street segment is the unit a municipality draws; terrain is how we approximate
        it where the streets are not published."""
        p = resolve(Evidence(n_junctions=391, n_streets=250, n_kerbs=2189,
                             dem_available=True, dem_resolution_m=1.0))
        assert p.method == schema.METHOD_JUNCTION_STREET

    def test_the_reason_says_what_was_missing(self):
        p = resolve(Evidence(n_junctions=391, dem_available=True, dem_resolution_m=1.0))
        assert "kerb" in p.reason.lower()
