"""Dry-weather flow from sewer service areas (ADR 0031)."""
from datetime import date, datetime, timedelta

import pytest

from swmmcanada.build import BuildConfig
from swmmcanada.build.assemble import assemble_inp
from swmmcanada.build.config import FlowUnits
from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     RainfallSeries, SewerServiceArea, SurfaceCatchment)
from swmmcanada.loading import (DwfAssumptions, LoadingTier, diurnal_pattern,
                                estimate_population, load_service_areas)
from swmmcanada.loading.dwf import dwf_lps, to_flow_units


class TestPopulationLadder:
    """Whichever rung answers is named in the result: an area resting on assumed density
    must stay distinguishable from one resting on a count."""

    def test_published_population_wins(self):
        e = estimate_population(SewerServiceArea("A", "N", 5.0, population=310.0,
                                                 dwelling_units=999), DwfAssumptions())
        assert e.tier is LoadingTier.MEASURED and e.people == 310.0

    def test_dwellings_beat_assumed_density(self):
        e = estimate_population(SewerServiceArea("A", "N", 5.0, dwelling_units=40),
                                DwfAssumptions())
        assert e.tier is LoadingTier.DWELLINGS and e.people == pytest.approx(96.0)

    def test_area_density_is_the_floor_and_says_so(self):
        e = estimate_population(SewerServiceArea("A", "N", 2.0), DwfAssumptions())
        assert e.tier is LoadingTier.AREA_DENSITY and "assumed" in e.basis

    def test_zero_dwellings_is_not_evidence(self):
        """`dwelling_units=0` means none were found, not that nobody lives there."""
        e = estimate_population(SewerServiceArea("A", "N", 2.0, dwelling_units=0),
                                DwfAssumptions())
        assert e.tier is LoadingTier.AREA_DENSITY


class TestFlow:
    def test_flow_is_population_times_coefficient_per_day(self):
        assert dwf_lps(1000, DwfAssumptions()) == pytest.approx(280_000 / 86400)

    def test_calibration_replaces_only_the_coefficient(self):
        a = DwfAssumptions().calibrated(215.0)
        assert a.litres_per_capita_day == 215.0
        assert a.source == "calibrated"
        assert a.persons_per_dwelling == DwfAssumptions().persons_per_dwelling

    def test_loading_source_travels_onto_every_area(self):
        res = load_service_areas([SewerServiceArea("A", "N", 1.0)],
                                 DwfAssumptions().calibrated(215.0))
        assert all(a.loading_source == "calibrated" for a in res.areas)


class TestDiagnosticsAreHonest:
    def test_tiers_are_counted(self):
        res = load_service_areas([
            SewerServiceArea("A", "N", 1.0),
            SewerServiceArea("B", "N", 1.0, dwelling_units=10),
            SewerServiceArea("C", "N", 1.0, population=50.0)])
        assert res.diagnostics["population_tiers"] == {
            "measured": 1, "dwellings": 1, "buildings": 0, "area_density": 1}
        assert res.diagnostics["pct_on_assumed_density"] == pytest.approx(33.3, abs=0.1)

    def test_the_accuracy_ceiling_is_stated(self):
        """The point of ADR 0031: resolution does not buy accuracy, the coefficient does."""
        d = load_service_areas([SewerServiceArea("A", "N", 1.0)]).diagnostics
        assert "coefficient" in d["accuracy_note"]
        assert d["coefficient_source"] == "synthetic"


class TestDiurnalPattern:
    def test_mean_is_one_so_volume_is_unchanged(self):
        _, f = diurnal_pattern()
        assert sum(f) / len(f) == pytest.approx(1.0, abs=1e-3)

    def test_shape_has_a_night_minimum_and_a_morning_peak(self):
        _, f = diurnal_pattern()
        assert min(f[1:5]) < 0.5 and max(f[6:10]) > 1.4
        assert len(f) == 24


class TestFlowUnitsConversion:
    """A 1000x error that still runs, still balances, and is nonsense."""

    def test_litres_per_second_becomes_cubic_metres_for_a_cms_model(self):
        assert to_flow_units(1.225, "CMS") == pytest.approx(0.001225)

    def test_lps_model_takes_the_value_as_is(self):
        assert to_flow_units(1.225, "LPS") == pytest.approx(1.225)

    def test_every_supported_flow_unit_has_a_conversion(self):
        for u in FlowUnits:
            assert to_flow_units(1.0, u.value) > 0

    def test_an_untabulated_unit_fails_loudly(self):
        with pytest.raises(ValueError, match="no litres/second conversion"):
            to_flow_units(1.0, "BANANAS")


def _model(service_areas, flow_units=FlowUnits.CMS):
    net = NetworkIn(
        junctions=[JunctionIn("J1", 10.0, 3.0, 0.0),
                   JunctionIn("SAN_M1", 9.0, 3.0, 0.0, system="sanitary")],
        outfalls=[OutfallIn("O1", 8.0, 0.0, 0.0),
                  OutfallIn("SAN_WWTP", 7.0, 0.0, 0.0, system="sanitary")],
        conduits=[ConduitIn("C1", "J1", "O1", 50.0),
                  ConduitIn("SAN_C1", "SAN_M1", "SAN_WWTP", 60.0, system="sanitary")])
    t0 = datetime(2024, 6, 1)
    rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)
    cfg = BuildConfig(out_dir="/tmp", start=date(2024, 6, 1), end=date(2024, 6, 2),
                      flow_units=flow_units)
    subs = [SurfaceCatchment("S1", "J1", 1.0, 50.0, 100.0, 1.0)]
    return assemble_inp(net, subs, rain, cfg, service_areas=service_areas)


class TestWriting:
    def test_dwf_and_pattern_sections_appear(self):
        areas = load_service_areas([SewerServiceArea("A", "SAN_M1", 2.0,
                                                     dwelling_units=120)]).areas
        txt = _model(areas).to_string()
        assert "[DWF]" in txt and "DWF_DIURNAL" in txt

    def test_areas_sharing_a_node_are_summed_not_overwritten(self):
        areas = load_service_areas([
            SewerServiceArea("A", "SAN_M1", 2.0, population=100.0),
            SewerServiceArea("B", "SAN_M1", 1.0, population=100.0)]).areas
        txt = _model(areas, FlowUnits.LPS).to_string()
        line = next(l for l in txt.splitlines() if l.startswith("SAN_M1 FLOW"))
        assert float(line.split()[2]) == pytest.approx(dwf_lps(200, DwfAssumptions()), rel=1e-3)

    def test_written_value_respects_the_model_flow_units(self):
        areas = load_service_areas([SewerServiceArea("A", "SAN_M1", 2.0,
                                                     population=1000.0)]).areas
        cms = next(l for l in _model(areas, FlowUnits.CMS).to_string().splitlines()
                   if l.startswith("SAN_M1 FLOW"))
        lps = next(l for l in _model(areas, FlowUnits.LPS).to_string().splitlines()
                   if l.startswith("SAN_M1 FLOW"))
        assert float(lps.split()[2]) == pytest.approx(1000 * float(cms.split()[2]), rel=1e-3)

    def test_no_service_areas_means_no_dwf_section(self):
        """The sanitary system stays an honest empty shell rather than gaining a fake one."""
        assert "[DWF]" not in _model([]).to_string()

    def test_service_areas_never_become_subcatchments(self):
        areas = load_service_areas([SewerServiceArea("A", "SAN_M1", 2.0,
                                                     population=100.0)]).areas
        txt = _model(areas).to_string()
        subcat_block = txt[txt.index("[SUBCATCHMENTS]"):txt.index("[SUBAREAS]")]
        assert "A" not in subcat_block.split()


class TestGuardIsWiredIn:
    """Unit-testing `_reject_service_areas` proves the function works, not that anything
    calls it. Deleting the call from `assemble_inp` left the whole suite green — found by
    reverting the fix and watching nothing fail, which is the check a test-after workflow
    owes itself."""

    def test_a_service_area_passed_as_a_subcatchment_is_refused_by_assemble(self):
        bad = SewerServiceArea(name="SSA_smuggled", node="SAN_M1", area_ha=1.0)
        net = NetworkIn(
            junctions=[JunctionIn("J1", 10.0, 3.0, 0.0)],
            outfalls=[OutfallIn("O1", 8.0, 0.0, 0.0)],
            conduits=[ConduitIn("C1", "J1", "O1", 50.0)])
        t0 = datetime(2024, 6, 1)
        rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)
        cfg = BuildConfig(out_dir="/tmp", start=date(2024, 6, 1), end=date(2024, 6, 2))
        with pytest.raises(TypeError, match="SSA_smuggled"):
            assemble_inp(net, [SurfaceCatchment("S1", "J1", 1.0, 50.0, 100.0, 1.0), bad],
                         rain, cfg)

    def test_the_guard_also_holds_through_build_model(self):
        """The datastore path calls build_model, not assemble_inp — the guard must cover
        the route the pipeline actually takes."""
        from swmmcanada.build.assemble import build_model

        bad = SewerServiceArea(name="SSA_smuggled2", node="SAN_M1", area_ha=1.0)
        net = NetworkIn(
            junctions=[JunctionIn("J1", 10.0, 3.0, 0.0)],
            outfalls=[OutfallIn("O1", 8.0, 0.0, 0.0)],
            conduits=[ConduitIn("C1", "J1", "O1", 50.0)])
        t0 = datetime(2024, 6, 1)
        rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)
        cfg = BuildConfig(out_dir="/tmp/guard-check", start=date(2024, 6, 1),
                          end=date(2024, 6, 2))
        with pytest.raises(TypeError, match="SSA_smuggled2"):
            build_model(network=net, subcatchments=[bad], rain=rain, config=cfg)
