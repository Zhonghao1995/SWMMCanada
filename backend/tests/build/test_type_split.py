"""SurfaceCatchment vs SewerServiceArea (ADR 0029 Q1/Q8).

The split exists to make one specific error impossible: writing a sanitary service area
into `[SUBCATCHMENTS]`, which routes roof and road runoff into a foul sewer — the
cross-connection municipalities run dedicated programmes to find and fix.
"""
import pytest

from swmmcanada.build.assemble import _reject_service_areas
from swmmcanada.build.models import SewerServiceArea, SurfaceCatchment, SurfaceCatchment


def surface(name="S1"):
    return SurfaceCatchment(name=name, outlet_node="J1", area_ha=1.0, pct_imperv=50.0,
                            width_m=100.0, pct_slope=1.0)


class TestTheTwoTypesAreGenuinelyDifferent:
    def test_service_area_has_no_runoff_parameters(self):
        """Sewage travels by lateral, not over the ground: width, slope, imperviousness and
        infiltration are meaningless for it, and their absence is the point."""
        fields = set(SewerServiceArea.__dataclass_fields__)
        for runoff_only in ("width_m", "pct_slope", "pct_imperv", "cn", "n_imperv",
                            "horton_f0_mm_h", "ga_ksat_mm_h"):
            assert runoff_only not in fields, runoff_only

    def test_they_share_only_geometry_and_identity(self):
        shared = (set(SurfaceCatchment.__dataclass_fields__)
                  & set(SewerServiceArea.__dataclass_fields__))
        assert shared == {"name", "area_ha", "polygon", "holes", "system"}

    def test_service_area_is_not_a_subclass(self):
        """A common base would invite the confusion the split exists to end."""
        assert not issubclass(SewerServiceArea, SurfaceCatchment)
        assert not issubclass(SurfaceCatchment, SewerServiceArea)

    def test_service_area_loads_a_node_not_an_outlet(self):
        a = SewerServiceArea(name="SSA1", node="SAN_MH1", area_ha=0.8)
        assert a.node == "SAN_MH1"
        assert not hasattr(a, "outlet_node")


class TestProvenanceIsSplitInTwo:
    """ADR 0031: an official boundary must not lend its authority to a handbook rate."""

    def test_geometry_and_loading_sources_are_independent(self):
        a = SewerServiceArea(name="SSA1", node="N", area_ha=1.0,
                             geometry_source="official", loading_source="synthetic")
        assert a.geometry_source == "official" and a.loading_source == "synthetic"

    def test_defaults_are_the_honest_ones(self):
        a = SewerServiceArea(name="SSA1", node="N", area_ha=1.0)
        assert a.geometry_source == "derived" and a.loading_source == "synthetic"

    def test_loading_evidence_travels_with_the_flow(self):
        a = SewerServiceArea(name="SSA1", node="N", area_ha=1.0, population=240.0,
                             dwelling_units=95, dwf_lps=0.97, loading_source="calibrated")
        assert (a.population, a.dwelling_units, a.dwf_lps) == (240.0, 95, 0.97)


class TestWriterGuard:
    def test_surface_catchments_pass(self):
        _reject_service_areas([surface(), surface("S2")])

    def test_a_service_area_is_refused(self):
        with pytest.raises(TypeError, match="never surface subcatchments"):
            _reject_service_areas([SewerServiceArea(name="SSA1", node="N", area_ha=1.0)])

    def test_one_service_area_hidden_among_many_is_still_caught(self):
        mixed = [surface(f"S{i}") for i in range(50)]
        mixed.insert(37, SewerServiceArea(name="SSA_sneaky", node="N", area_ha=1.0))
        with pytest.raises(TypeError, match="SSA_sneaky"):
            _reject_service_areas(mixed)

    def test_the_error_says_where_they_should_go(self):
        with pytest.raises(TypeError, match=r"\[DWF\]/\[RDII\]"):
            _reject_service_areas([SewerServiceArea(name="SSA1", node="N", area_ha=1.0)])

    def test_empty_is_fine(self):
        _reject_service_areas([])


class TestMigrationIsComplete:
    """The bridge was deleted once the last consumer moved (ADR 0029 Q8). Keeping it would
    have left the ambiguous word as the thing a developer reaches for by default, and
    "subcatchment" meaning two different things is what started this work.

    Full coverage of the removal lives in test_migration_complete.py."""

    def test_the_old_name_is_gone(self):
        import swmmcanada.build.models as models

        assert not hasattr(models, "SubcatchmentIn")
