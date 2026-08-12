"""Exporters honour the system selection (ADR 0029 Q3).

Exporters shipped storm only, with a note in their field docs saying so. Now that combined
pipes carry their own tag and every wastewater system reaches a destination, "storm only"
would quietly drop the mains that carry most of a combined city's stormwater.
"""
from datetime import datetime, timedelta

import pytest

from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     RainfallSeries)

_T0 = datetime(2024, 6, 1)
_RAIN = RainfallSeries([_T0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)


class _DS:
    """Datastore-shaped stub: storm and combined wired together as a real combined city
    has them, sanitary separate behind its own namespace."""

    network = NetworkIn(
        junctions=[JunctionIn("S1", 10.0, -123.360, 48.420),
                   JunctionIn("K1", 10.0, -123.361, 48.421, system="combined"),
                   JunctionIn("SAN_M1", 9.0, -123.362, 48.422, system="sanitary")],
        outfalls=[OutfallIn("STORM_OUT", 5.0, -123.363, 48.423),
                  OutfallIn("SAN_WWTP", 4.0, -123.364, 48.424, system="sanitary")],
        conduits=[ConduitIn("s1", "S1", "STORM_OUT", 50.0),
                  ConduitIn("k1", "K1", "S1", 50.0, system="combined"),
                  ConduitIn("n1", "SAN_M1", "SAN_WWTP", 50.0, system="sanitary")])
    subcatchments = []
    service_areas = []
    config = {"coordinate_crs": None}
    provenance = {}
    rain = _RAIN
    evaporation = None
    temperature = None
    tide = None


def _exporter(name):
    if name == "mikeplus":
        from swmmcanada.export.mikeplus import MikePlusExporter
        return MikePlusExporter()
    from swmmcanada.export.icm import IcmExporter
    return IcmExporter()


@pytest.mark.parametrize("target", ["mikeplus", "icm"])
class TestSelection:
    def test_default_includes_every_system_present(self, target, tmp_path):
        """A combined city's combined mains carry its stormwater; excluding them by default
        exports a fraction of the drainage and calls it the storm system."""
        res = _exporter(target).export(_DS(), tmp_path / target)
        assert set(res.view["systems"]) == {"storm_minor", "combined", "sanitary"}

    def test_an_explicit_selection_is_honoured(self, target, tmp_path):
        res = _exporter(target).export(_DS(), tmp_path / target, systems=["sanitary"])
        assert res.view["systems"] == ["sanitary"]
        assert res.view["n_junctions"] == 1 and res.view["n_outfalls"] == 1

    def test_selecting_storm_alone_drops_the_combined_mains(self, target, tmp_path):
        res = _exporter(target).export(_DS(), tmp_path / target, systems=["storm_minor"])
        assert res.view["n_junctions"] == 1, "combined node must not ride along"

    def test_the_view_reports_what_filtering_cost(self, target, tmp_path):
        res = _exporter(target).export(_DS(), tmp_path / target, systems=["combined"])
        # K1 drains through a storm node to the outfall; alone it reaches nothing.
        assert res.view["n_orphaned_nodes"] == 1
        assert "K1" in res.view["orphaned_sample"]

    def test_a_clean_selection_orphans_nothing(self, target, tmp_path):
        res = _exporter(target).export(_DS(), tmp_path / target, systems=["sanitary"])
        assert res.view["n_orphaned_nodes"] == 0


@pytest.mark.parametrize("target", ["mikeplus", "icm"])
def test_the_package_states_which_systems_it_holds(target, tmp_path):
    """A package that does not say which systems it holds cannot be told apart from one
    holding all of them — the reason the old 'storm system only' note existed at all."""
    res = _exporter(target).export(_DS(), tmp_path / target, systems=["sanitary"])
    notes = next(p for p in res.files if p.name == "field_mapping.md").read_text()
    assert "sanitary" in notes
    assert "storm system only" not in notes.lower()


@pytest.mark.parametrize("target", ["mikeplus", "icm"])
def test_the_package_warns_when_the_view_orphaned_something(target, tmp_path):
    res = _exporter(target).export(_DS(), tmp_path / target, systems=["combined"])
    notes = next(p for p in res.files if p.name == "field_mapping.md").read_text()
    assert "lose their route to an outfall" in notes
