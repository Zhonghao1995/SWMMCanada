"""Per-system export views (ADR 0029 Q3).

"Types apart, model together": one hydraulic model carries every system, and an export is a
*filtered view* of it. The user picks which systems to include; nothing is split into
separate models, because the interaction between them is the reason they share one.

The sharp edge is that filtering a connected graph by tag can cut a component off from its
outfall. A view that silently ships an orphaned network looks like a model and is not one.
"""
import pytest

from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     filter_system)


def J(n, s="storm_minor"):
    return JunctionIn(n, 10.0, 0.0, 0.0, system=s)


def O(n, s="storm_minor"):
    return OutfallIn(n, 5.0, 0.0, 0.0, system=s)


def C(n, a, b, s="storm_minor"):
    return ConduitIn(n, a, b, 50.0, system=s)


def three_system_model():
    """Storm and combined wired together (as they are in a real combined city), sanitary
    separate behind its own namespace."""
    return NetworkIn(
        junctions=[J("S1"), J("K1", "combined"), J("SAN_M1", "sanitary")],
        outfalls=[O("STORM_OUT"), O("SAN_WWTP", "sanitary")],
        conduits=[C("s1", "S1", "STORM_OUT"),
                  C("k1", "K1", "S1", "combined"),
                  C("n1", "SAN_M1", "SAN_WWTP", "sanitary")])


class TestSingleSystemStillWorks:
    def test_a_bare_string_selects_one_system(self):
        v = filter_system(three_system_model(), "sanitary")
        assert [j.name for j in v.junctions] == ["SAN_M1"]
        assert [o.name for o in v.outfalls] == ["SAN_WWTP"]

    def test_the_default_is_unchanged(self):
        v = filter_system(three_system_model())
        assert [j.name for j in v.junctions] == ["S1"]


class TestMultiSelect:
    def test_several_systems_can_be_selected_together(self):
        v = filter_system(three_system_model(), ["storm_minor", "combined"])
        assert {j.name for j in v.junctions} == {"S1", "K1"}
        assert {c.name for c in v.conduits} == {"s1", "k1"}

    def test_selecting_everything_returns_the_whole_model(self):
        net = three_system_model()
        v = filter_system(net, ["storm_minor", "combined", "sanitary"])
        assert len(v.junctions) == len(net.junctions)
        assert len(v.conduits) == len(net.conduits)

    def test_a_link_is_kept_only_if_both_of_its_nodes_survive(self):
        """A conduit tagged combined joins a storm node. Dropping storm must drop the link
        too, or the view references a node it does not contain."""
        v = filter_system(three_system_model(), ["combined"])
        names = {j.name for j in v.junctions} | {o.name for o in v.outfalls}
        for c in v.conduits:
            assert c.from_node in names and c.to_node in names

    def test_an_empty_selection_yields_an_empty_view(self):
        v = filter_system(three_system_model(), [])
        assert not v.junctions and not v.conduits and not v.outfalls


class TestSeveredComponentsAreReported:
    """Ottawa's storm and combined networks share one node and Toronto's share none, so
    this rarely bites — but 'rarely' is exactly when a silent failure survives to
    production."""

    def test_filtering_that_orphans_a_component_is_reported(self):
        from swmmcanada.build.models import filter_system_report

        # Storm drains to its outfall THROUGH a combined trunk; dropping combined strands it.
        net = NetworkIn(
            junctions=[J("S1"), J("K1", "combined")],
            outfalls=[O("OUT", "combined")],
            conduits=[C("s1", "S1", "K1", "storm_minor"),
                      C("k1", "K1", "OUT", "combined")])
        _, report = filter_system_report(net, ["storm_minor"])
        assert report["n_orphaned_nodes"] >= 1
        assert "S1" in report["orphaned_sample"]

    def test_a_clean_view_reports_nothing_orphaned(self):
        _, report = filter_system_report_of(three_system_model(), ["sanitary"])
        assert report["n_orphaned_nodes"] == 0

    def test_the_report_names_which_systems_were_selected(self):
        _, report = filter_system_report_of(three_system_model(), ["storm_minor", "combined"])
        assert set(report["systems"]) == {"storm_minor", "combined"}


def filter_system_report_of(net, systems):
    from swmmcanada.build.models import filter_system_report
    return filter_system_report(net, systems)
