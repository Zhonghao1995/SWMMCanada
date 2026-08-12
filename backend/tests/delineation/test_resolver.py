"""The resolver is the sole method-selection entry point (ADR 0029 Q10/Q11)."""
import pytest

from swmmcanada.delineation import DelineationPlan, Evidence, resolve
from swmmcanada.validate import schema


class TestChoices:
    def test_inlets_and_land_give_the_parcel_method(self):
        p = resolve(Evidence(n_catchbasins=7864, n_parcels=15728, n_buildings=21969))
        assert (p.method, p.anchors, p.shaping) == ("catchbasin_parcel", "catch_basin", "parcel")

    def test_inlets_without_land_stay_seeded_but_geometric(self):
        p = resolve(Evidence(n_catchbasins=1200))
        assert p.method == schema.METHOD_CATCHBASIN_VORONOI and p.anchors == "catch_basin"

    def test_no_inlets_with_a_dem_goes_to_terrain(self):
        p = resolve(Evidence(n_junctions=400, dem_available=True))
        assert (p.method, p.shaping) == ("junction_dem", "dem_d8")

    def test_no_inlets_no_dem_is_the_honest_floor(self):
        p = resolve(Evidence(n_junctions=400))
        assert p.method == schema.METHOD_JUNCTION_VORONOI and p.confidence == "low"

    def test_buildings_alone_count_as_land(self):
        """Ottawa publishes buildings but no parcels; that is still real land evidence."""
        p = resolve(Evidence(n_catchbasins=900, n_buildings=5000))
        assert p.method == "catchbasin_parcel"

    def test_resolve_is_total(self):
        assert resolve(Evidence()).method == schema.METHOD_JUNCTION_VORONOI


class TestEvidenceIsRuntimeNotCityLevel:
    def test_a_city_with_inlets_but_none_in_this_aoi_falls_through(self):
        """Victoria publishes 7,864 catch basins and a lakeside AOI can contain none. A
        static capability row would claim inlets are available and be wrong here."""
        p = resolve(Evidence(n_catchbasins=0, n_parcels=500, n_junctions=40,
                             dem_available=True, city="victoria"))
        assert p.anchors == "junction"


class TestOfficialBasinsBoundOnly:
    def test_an_official_layer_bounds_but_never_selects(self):
        """Phase 0: every official polygon layer measured across 36 cities is a macro
        basin. It constrains where units may go; it does not become the units."""
        with_basin = resolve(Evidence(n_catchbasins=100, n_parcels=100,
                                      official_basin_level="level_2"))
        without = resolve(Evidence(n_catchbasins=100, n_parcels=100))
        assert with_basin.boundary == "official_basin"
        assert without.boundary == "aoi"
        assert with_basin.method == without.method

    def test_review_required_does_not_bound(self):
        p = resolve(Evidence(n_catchbasins=100, n_parcels=100,
                             official_basin_level="level_2_review_required"))
        assert p.boundary == "aoi"


class TestPlansExplainThemselves:
    """ADR 0029 Q11: never a bare label."""

    @pytest.mark.parametrize("ev", [
        Evidence(n_catchbasins=100, n_parcels=100),
        Evidence(n_catchbasins=100),
        Evidence(n_junctions=10, dem_available=True),
        Evidence(),
    ])
    def test_every_plan_carries_reason_gates_and_evidence(self, ev):
        p = resolve(ev)
        assert p.reason and p.gates and isinstance(p.evidence, dict)
        assert set(p.gates) == {"inlets_present", "land_present", "dem_present",
                                "kerb_usable"}

    def test_reason_quotes_the_numbers_it_used(self):
        p = resolve(Evidence(n_catchbasins=7864, n_parcels=15728, n_buildings=21969))
        assert "7864" in p.reason and "15728" in p.reason

    def test_voronoi_never_claims_to_be_a_delineation(self):
        r = resolve(Evidence(n_junctions=10)).reason.lower()
        assert "geometric" in r and "not hydrological" in r

    def test_plan_serialises_for_provenance(self):
        d = resolve(Evidence(n_catchbasins=5, n_parcels=5)).as_dict()
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
        assert plan.method == "catchbasin_parcel"
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


class TestKerbConditionedDemIsAMethod:
    """规划书 §4 priority 2: inlets as drainage targets, kerb-conditioned terrain, D8.

    Not a new algorithm — the same DEM delineator with inlets as pour points and the kerb
    geometry as an extra input (ADR 0029 Q10). The resolver's job is to notice when all
    three are present."""

    def test_inlets_plus_kerbs_plus_terrain_pick_the_kerb_method(self):
        from swmmcanada.validate import schema

        p = resolve(Evidence(n_catchbasins=773, n_parcels=4749, n_buildings=499,
                             n_kerbs=2189, dem_available=True, dem_resolution_m=1.0))
        assert p.method == schema.METHOD_CATCHBASIN_DEM
        assert p.anchors == "catch_basin" and p.shaping == "dem_d8"

    def test_without_kerbs_it_stays_on_the_parcel_method(self):
        """Thirty cities publish none, and must be left where they were."""
        p = resolve(Evidence(n_catchbasins=773, n_parcels=4749, n_buildings=499,
                             dem_available=True, dem_resolution_m=1.0))
        assert p.method == "catchbasin_parcel"

    def test_kerbs_without_terrain_cannot_use_them(self):
        """A kerb only means something as an edit to a surface."""
        p = resolve(Evidence(n_catchbasins=773, n_parcels=4749, n_kerbs=2189,
                             dem_available=False))
        assert p.method == "catchbasin_parcel"

    def test_kerbs_on_a_coarse_dem_are_not_worth_using(self):
        """A 150 mm kerb cannot be represented on a 30 m posting; claiming to condition
        with it would be theatre."""
        p = resolve(Evidence(n_catchbasins=773, n_parcels=4749, n_kerbs=2189,
                             dem_available=True, dem_resolution_m=30.0))
        assert p.method == "catchbasin_parcel"
        assert p.gates["kerb_usable"] is False

    def test_the_reason_names_the_evidence(self):
        p = resolve(Evidence(n_catchbasins=773, n_parcels=4749, n_kerbs=2189,
                             dem_available=True, dem_resolution_m=1.0))
        assert "2189" in p.reason and "kerb" in p.reason.lower()
