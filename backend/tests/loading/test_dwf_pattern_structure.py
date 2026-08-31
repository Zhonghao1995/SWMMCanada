"""DWF pattern structure: optional monthly + hourly + weekend patterns (ticket 10).

SWMM gives every [DWF] line up to four pattern slots; municipal practice structures
dry-weather flow as monthly x hourly x weekend. The pattern group is loading
configuration: `load_service_areas` stamps the group onto the areas, the writer emits
exactly what was stamped, and a build that does not opt in must produce byte-for-byte
the single-hourly output it produced before this ticket existed.

The first version is structure-first: the weekend curve reuses the weekday diurnal
shape and the monthly factors are neutral 1.0, so opting in changes the model's
STRUCTURE (three named patterns, ready for a city's evidence) without changing a
single simulated number until measured curves replace the placeholders.
"""
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from swmm_api import read_inp_file
from swmm_api.input_file import SEC
from swmmcanada.build import BuildConfig
from swmmcanada.build.assemble import assemble_inp
from swmmcanada.build.config import FlowUnits
from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     RainfallSeries, SewerServiceArea, SurfaceCatchment)
from swmmcanada.loading import load_service_areas
from swmmcanada.loading.dwf import (DIURNAL_PATTERN_NAME, MONTHLY_PATTERN_NAME,
                                    PATTERN_STRUCTURE_DEFAULT, WEEKEND_PATTERN_NAME,
                                    diurnal_pattern, dwf_pattern_group)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "dwf" / "single_pattern_baseline.inp"
THREE = ("monthly", "hourly", "weekend")


# --- the pattern group (loading configuration) ---------------------------------


class TestPatternGroup:
    def test_default_is_the_single_hourly_pattern_unchanged(self):
        group = dwf_pattern_group()
        assert [(n, c) for n, c, _ in group] == [(DIURNAL_PATTERN_NAME, "HOURLY")]
        name, factors = diurnal_pattern()
        assert group[0][0] == name and group[0][2] == list(factors)

    def test_three_pattern_structure_yields_all_three_cycles(self):
        group = dwf_pattern_group(THREE)
        assert [(n, c) for n, c, _ in group] == [
            (MONTHLY_PATTERN_NAME, "MONTHLY"),
            (DIURNAL_PATTERN_NAME, "HOURLY"),
            (WEEKEND_PATTERN_NAME, "WEEKEND")]
        lengths = {c: len(f) for _, c, f in group}
        assert lengths == {"MONTHLY": 12, "HOURLY": 24, "WEEKEND": 24}

    @pytest.mark.parametrize("structure", [PATTERN_STRUCTURE_DEFAULT, THREE])
    def test_every_pattern_has_mean_one_so_no_pattern_scales_the_volume(self, structure):
        """The normalisation guard: patterns redistribute the average day, they must
        never quietly multiply it."""
        for name, _, factors in dwf_pattern_group(structure):
            assert sum(factors) / len(factors) == pytest.approx(1.0, abs=1e-3), name

    def test_structure_first_placeholders_change_no_numbers(self):
        """Pinned on purpose: weekend = the weekday shape, monthly = neutral 1.0 — the
        structure lands now, the values wait for a city's own flow record."""
        group = {c: f for _, c, f in dwf_pattern_group(THREE)}
        assert group["WEEKEND"] == group["HOURLY"]
        assert group["MONTHLY"] == [1.0] * 12

    def test_an_unknown_structure_key_fails_loudly(self):
        with pytest.raises(ValueError, match="daily"):
            dwf_pattern_group(("hourly", "daily"))


# --- loading stamps the group onto the areas -----------------------------------


class TestLoadingStampsTheGroup:
    def test_default_stamp_and_record_are_the_single_hourly_pattern(self):
        res = load_service_areas([SewerServiceArea("A", "N", 1.0, population=100.0)])
        assert all(a.dwf_pattern == DIURNAL_PATTERN_NAME for a in res.areas)
        assert res.diagnostics["dwf_pattern_structure"] == ["hourly"]

    def test_three_pattern_stamp_lists_the_names_in_structure_order(self):
        res = load_service_areas([SewerServiceArea("A", "N", 1.0, population=100.0)],
                                 pattern_structure=THREE)
        assert res.areas[0].dwf_pattern == (
            f"{MONTHLY_PATTERN_NAME} {DIURNAL_PATTERN_NAME} {WEEKEND_PATTERN_NAME}")
        assert res.diagnostics["dwf_pattern_structure"] == list(THREE)


# --- the writer emits what was stamped ------------------------------------------


def _baseline_model(pattern_structure=None):
    """The exact model the committed byte-baseline fixture was generated from."""
    net = NetworkIn(
        junctions=[JunctionIn("J1", 10.0, 3.0, 0.0),
                   JunctionIn("SAN_M1", 9.0, 3.0, 0.0, system="sanitary"),
                   JunctionIn("SAN_M2", 8.5, 3.1, 0.0, system="sanitary")],
        outfalls=[OutfallIn("O1", 8.0, 0.0, 0.0),
                  OutfallIn("SAN_WWTP", 7.0, 0.0, 0.0, system="sanitary")],
        conduits=[ConduitIn("C1", "J1", "O1", 50.0),
                  ConduitIn("SAN_C1", "SAN_M1", "SAN_M2", 60.0, system="sanitary"),
                  ConduitIn("SAN_C2", "SAN_M2", "SAN_WWTP", 60.0, system="sanitary")])
    t0 = datetime(2024, 6, 1)
    rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)
    cfg = BuildConfig(out_dir="/tmp", start=date(2024, 6, 1), end=date(2024, 6, 2),
                      flow_units=FlowUnits.CMS)
    subs = [SurfaceCatchment("S1", "J1", 1.0, 50.0, 100.0, 1.0)]
    kw = {"pattern_structure": pattern_structure} if pattern_structure else {}
    areas = load_service_areas([
        SewerServiceArea("A", "SAN_M1", 2.0, dwelling_units=120),
        SewerServiceArea("B", "SAN_M1", 1.0, population=100.0),
        SewerServiceArea("C", "SAN_M2", 1.5, population=250.0)], **kw).areas
    return assemble_inp(net, subs, rain, cfg, service_areas=areas)


class TestWriter:
    def test_not_opting_in_is_byte_identical_to_the_pre_ticket_output(self):
        """The committed fixture was generated by the pre-ticket writer. A build that
        does not select a structure must still produce exactly those bytes."""
        assert _baseline_model().to_string() == FIXTURE.read_text()

    def test_three_patterns_are_written_and_referenced(self):
        txt = _baseline_model(THREE).to_string()
        patterns = txt.split("[PATTERNS]")[1].split("[")[0]
        assert f"{MONTHLY_PATTERN_NAME} MONTHLY" in patterns
        assert f"{DIURNAL_PATTERN_NAME} HOURLY" in patterns
        assert f"{WEEKEND_PATTERN_NAME} WEEKEND" in patterns
        dwf_line = next(l for l in txt.splitlines() if l.startswith("SAN_M1 FLOW"))
        assert dwf_line.split()[3:] == [MONTHLY_PATTERN_NAME, DIURNAL_PATTERN_NAME,
                                       WEEKEND_PATTERN_NAME]

    def test_the_three_pattern_model_reads_back_losslessly(self, tmp_path):
        """swmm-api must read back exactly the factors and references we wrote."""
        path = tmp_path / "three.inp"
        _baseline_model(THREE).write_file(str(path))
        back = read_inp_file(str(path))
        expected = {n: f for n, _, f in dwf_pattern_group(THREE)}
        got = back[SEC.PATTERNS]
        assert set(got) == set(expected)
        for name, factors in expected.items():
            assert list(got[name].factors) == pytest.approx(factors)
        dwf = back[SEC.DWF][("SAN_M1", "FLOW")]
        assert [dwf.pattern1, dwf.pattern2, dwf.pattern3] == [
            MONTHLY_PATTERN_NAME, DIURNAL_PATTERN_NAME, WEEKEND_PATTERN_NAME]

    def test_an_unstamped_area_falls_back_to_the_single_hourly_pattern(self):
        """Hand-made areas (and pre-ticket datastores) carry dwf_lps but may carry no
        stamp; they must keep getting exactly the pre-ticket single pattern."""
        net = NetworkIn(
            junctions=[JunctionIn("SAN_M1", 9.0, 3.0, 0.0, system="sanitary")],
            outfalls=[OutfallIn("SAN_WWTP", 7.0, 0.0, 0.0, system="sanitary")],
            conduits=[ConduitIn("SAN_C1", "SAN_M1", "SAN_WWTP", 60.0, system="sanitary")])
        t0 = datetime(2024, 6, 1)
        rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)
        cfg = BuildConfig(out_dir="/tmp", start=date(2024, 6, 1), end=date(2024, 6, 2))
        bare = SewerServiceArea("A", "SAN_M1", 1.0, dwf_lps=0.5)
        txt = assemble_inp(net, [], rain, cfg, service_areas=[bare]).to_string()
        assert f"SAN_M1 FLOW 0.0005 {DIURNAL_PATTERN_NAME}" in txt
        assert f"{DIURNAL_PATTERN_NAME} HOURLY" in txt

    def test_areas_stamped_with_different_groups_fail_loudly(self):
        """One build, one loading configuration. Two stamps means the configuration
        split somewhere upstream — a model that silently averaged them would run and
        be wrong."""
        from dataclasses import replace

        net = NetworkIn(
            junctions=[JunctionIn("SAN_M1", 9.0, 3.0, 0.0, system="sanitary"),
                       JunctionIn("SAN_M2", 8.5, 3.1, 0.0, system="sanitary")],
            outfalls=[OutfallIn("SAN_WWTP", 7.0, 0.0, 0.0, system="sanitary")],
            conduits=[ConduitIn("SAN_C1", "SAN_M1", "SAN_M2", 60.0, system="sanitary"),
                      ConduitIn("SAN_C2", "SAN_M2", "SAN_WWTP", 60.0, system="sanitary")])
        t0 = datetime(2024, 6, 1)
        rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)
        cfg = BuildConfig(out_dir="/tmp", start=date(2024, 6, 1), end=date(2024, 6, 2))
        one = load_service_areas([SewerServiceArea("A", "SAN_M1", 1.0, population=50.0)]).areas
        other = load_service_areas([SewerServiceArea("B", "SAN_M2", 1.0, population=50.0)],
                                   pattern_structure=THREE).areas
        with pytest.raises(ValueError, match="pattern"):
            assemble_inp(net, [], rain, cfg, service_areas=one + other)


# --- municipal practice declares the structure preference -----------------------


class TestPracticeDeclaresTheStructure:
    def test_a_followed_registered_structure_reaches_the_overrides(self):
        from swmmcanada.sources.cities.practice import (municipal_practice,
                                                        practice_build_overrides)

        overrides = practice_build_overrides(municipal_practice("vancouver"))
        assert overrides["dwf_pattern_structure"] == ["monthly", "hourly", "weekend"]

    def test_no_record_declares_no_structure(self):
        from swmmcanada.sources.cities.practice import practice_build_overrides

        assert practice_build_overrides(None)["dwf_pattern_structure"] is None

    def test_the_field_counts_as_consumed_under_follow(self):
        from swmmcanada.sources.cities.practice import practice_provenance

        block = practice_provenance("vancouver", follow=True)
        assert "dwf_pattern_structure" in block["consumed"]
        assert "dwf_pattern_structure" not in block["information_only"]
