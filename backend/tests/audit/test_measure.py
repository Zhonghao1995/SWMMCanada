"""Level judgement (ADR 0030). The calibration points are live measurements from
2026-08-12; if a threshold moves, these are the numbers it has to keep explaining."""
import pytest

from swmmcanada.audit.measure import (RATIO_LEVEL_1_CANDIDATE, RATIO_LEVEL_2, Level, judge)
from swmmcanada.sources.cities.capability import Role


def layer(role, system, n, name="L"):
    return {"role": role, "system": system, "n_features": n, "layer_name": name}


VICTORIA_SANITARY = [layer("subcatchment", "sanitary", 57, "Sewer SubCatchment Areas"),
                     layer("gravity_main", "sanitary", 4638, "Sewer Gravity Mains")]
VICTORIA_STORM = [layer("catchment", "storm", 64, "Storm Drain Catchment Areas"),
                  layer("catch_basin", "storm", 7864, "Storm Drain Catch Basins")]
# Hamilton: 8,147 areas, one per length of combined sewer.
HAMILTON_COMBINED = [layer("subcatchment", "combined", 8147, "Combined Catchment Areas"),
                     layer("gravity_main", "combined", 8500, "Combined Mains")]


class TestCalibrationPoints:
    def test_a_layer_named_subcatchment_can_still_be_level_2(self):
        """The whole reason `granularity` is machine-measured. Victoria's layer is *called*
        "Sewer SubCatchment Areas" and is a pump-station basin (30.7 ha median)."""
        v = judge(VICTORIA_SANITARY, "sanitary")
        assert v.level is Level.LEVEL_2
        assert v.anchor_ratio == pytest.approx(57 / 4638, rel=1e-3)

    def test_victoria_storm_is_level_2(self):
        v = judge(VICTORIA_STORM, "storm")
        assert v.level is Level.LEVEL_2
        assert v.anchor_role == Role.CATCH_BASIN.value

    def test_per_segment_areas_reach_level_1_candidate(self):
        v = judge(HAMILTON_COMBINED, "combined")
        assert v.level is Level.LEVEL_1_CANDIDATE
        assert v.anchor_role == Role.GRAVITY_MAIN.value

    def test_calibration_points_stay_two_orders_apart(self):
        """The thresholds are only separable because the real data is. If a future fleet
        scan closes this gap, the thresholds need re-deriving, not nudging."""
        coarse = judge(VICTORIA_SANITARY, "sanitary").anchor_ratio
        fine = judge(HAMILTON_COMBINED, "combined").anchor_ratio
        assert fine / coarse > 50


class TestVerdictsExplainThemselves:
    """ADR 0029 Q11: never return a bare Level."""

    @pytest.mark.parametrize("rows,system", [(VICTORIA_SANITARY, "sanitary"),
                                             (VICTORIA_STORM, "storm"),
                                             (HAMILTON_COMBINED, "combined"),
                                             ([], "storm")])
    def test_every_verdict_carries_a_reason(self, rows, system):
        assert judge(rows, system).reason

    def test_verdict_names_the_layers_it_used(self):
        v = judge(VICTORIA_SANITARY, "sanitary")
        assert "Sewer SubCatchment Areas" in v.evidence["polygon_layers"]
        assert "Sewer Gravity Mains" in v.evidence["anchor_layers"]

    def test_alternate_anchor_ratio_is_published(self):
        """When more than one anchor exists the choice must be visible and arguable."""
        rows = HAMILTON_COMBINED + [layer("catch_basin", "combined", 30000, "Combined CB")]
        v = judge(rows, "combined")
        assert v.anchor_role == Role.CATCH_BASIN.value
        assert v.evidence["anchor_ratio_alternates"]["gravity_main"] == pytest.approx(
            8147 / 8500, rel=1e-3)


class TestRefusals:
    def test_no_polygon_layer_is_none_not_a_guess(self):
        assert judge([layer("catch_basin", "storm", 500)], "storm").level is Level.NONE

    def test_polygons_without_an_anchor_go_to_review(self):
        """Ungradable is not the same as coarse. Calling it Level 1 would be the Victoria
        mistake with no counter-evidence at all."""
        v = judge([layer("subcatchment", "sanitary", 3000)], "sanitary")
        assert v.level is Level.LEVEL_2_REVIEW
        assert v.n_polygons == 3000 and v.anchor_ratio is None

    def test_unknown_system_rows_are_not_borrowed(self):
        """An unattributed count could push a ratio across a threshold invisibly."""
        rows = [layer("subcatchment", "sanitary", 4000), layer("gravity_main", None, 4200)]
        assert judge(rows, "sanitary").level is Level.LEVEL_2_REVIEW

    def test_failed_gate_demotes_a_qualifying_ratio(self):
        v = judge(HAMILTON_COMBINED, "combined", gates={"coverage": True, "mappable": False})
        assert v.level is Level.LEVEL_2_REVIEW
        assert "mappable" in v.reason

    def test_level_1_is_never_granted_statically(self):
        """Promotion needs official-outlet agreement, which needs a build first."""
        for rows, sysname in [(VICTORIA_SANITARY, "sanitary"), (HAMILTON_COMBINED, "combined")]:
            assert judge(rows, sysname).level is not Level.LEVEL_1


def test_threshold_band_is_ordered():
    assert 0 < RATIO_LEVEL_2 < RATIO_LEVEL_1_CANDIDATE < 1
