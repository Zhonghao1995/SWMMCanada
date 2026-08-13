"""Wastewater systems terminate somewhere real (ADR 0029 Q4).

A combined sewer has two genuine destinations: dry weather goes to the treatment plant
through an interceptor, storm weather overflows to a watercourse through a CSO. Modelling
only one is wrong in a specific way — with no overflow the CSO discharge is identically
zero, and CSO discharge is the single output a combined system exists to be asked about.

Phase 0 (2026-08-12) measured the fleet: CSO structures are published by no supported city
and interceptors by two, so almost every build takes the synthetic boundary path. It must
therefore be honest rather than convenient.
"""
import pytest

from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn
from swmmcanada.delineation.outlet import ensure_wastewater_outlet


def J(name, invert, system="sanitary"):
    return JunctionIn(name, invert, 0.0, 0.0, system=system)


def C(name, a, b, system="sanitary"):
    return ConduitIn(name, a, b, 50.0, system=system)


def chain(system="sanitary", n=4):
    """A gravity chain falling downhill with no outfall of its own."""
    js = [J(f"M{i}", 10.0 - i, system) for i in range(n)]
    return NetworkIn(junctions=js, outfalls=[],
                     conduits=[C(f"c{i}", f"M{i}", f"M{i+1}", system) for i in range(n - 1)])


class TestSyntheticBoundary:
    def test_a_wastewater_network_with_no_outfall_gets_one(self):
        net, diag = ensure_wastewater_outlet(chain(), system="sanitary")
        assert len(net.outfalls) == 1
        assert diag["n_added"] == 1

    def test_the_added_outfall_carries_the_system_tag(self):
        net, _ = ensure_wastewater_outlet(chain("combined"), system="combined")
        assert net.outfalls[0].system == "combined"

    def test_it_attaches_at_the_downstream_end(self):
        """The plant is downstream, not wherever happens to be first in the list."""
        net, _ = ensure_wastewater_outlet(chain(n=4), system="sanitary")
        link = next(c for c in net.conduits if c.to_node == net.outfalls[0].name)
        assert link.from_node == "M3", "must hang off the lowest node in the chain"

    def test_the_boundary_sits_below_the_node_it_serves(self):
        net, _ = ensure_wastewater_outlet(chain(), system="sanitary")
        terminal = next(j for j in net.junctions if j.name == "M3")
        assert net.outfalls[0].invert_m < terminal.invert_m

    def test_it_is_labelled_synthetic_not_published(self):
        _, diag = ensure_wastewater_outlet(chain(), system="sanitary")
        assert diag["provenance"] == "synthetic"
        assert "interceptor" in diag["reason"] or "treatment" in diag["reason"]


class TestItNeverBorrowsAStormOutfall:
    """Ottawa publishes 13 outfalls and not one of them takes combined flow. Borrowing one
    would fabricate a destination the city does not have."""

    def test_a_storm_outfall_does_not_satisfy_a_wastewater_system(self):
        base = chain()
        net = NetworkIn(
            junctions=list(base.junctions) + [J("S1", 12.0, "storm_minor")],
            outfalls=[OutfallIn("STORM_OUT", 5.0, 0.0, 0.0, system="storm_minor")],
            conduits=list(base.conduits) + [C("sc", "S1", "STORM_OUT", "storm_minor")])
        out, diag = ensure_wastewater_outlet(net, system="sanitary")
        assert diag["n_added"] == 1
        added = [o for o in out.outfalls if o.system == "sanitary"]
        assert len(added) == 1 and added[0].name != "STORM_OUT"

    def test_the_storm_system_is_left_untouched(self):
        base = chain()
        net = NetworkIn(
            junctions=list(base.junctions) + [J("S1", 12.0, "storm_minor")],
            outfalls=[OutfallIn("STORM_OUT", 5.0, 0.0, 0.0, system="storm_minor")],
            conduits=list(base.conduits) + [C("sc", "S1", "STORM_OUT", "storm_minor")])
        out, _ = ensure_wastewater_outlet(net, system="sanitary")
        assert [o.name for o in out.outfalls if o.system == "storm_minor"] == ["STORM_OUT"]


class TestItDoesNotActWhenItShouldNot:
    def test_a_system_that_already_reaches_an_outfall_is_left_alone(self):
        base = chain()
        net = NetworkIn(
            junctions=base.junctions,
            outfalls=[OutfallIn("WWTP", 5.0, 0.0, 0.0, system="sanitary")],
            conduits=list(base.conduits) + [C("last", "M3", "WWTP")])
        out, diag = ensure_wastewater_outlet(net, system="sanitary")
        assert diag["n_added"] == 0 and len(out.outfalls) == 1

    def test_a_network_without_that_system_is_untouched(self):
        net = NetworkIn(
            junctions=[J("S1", 10.0, "storm_minor")],
            outfalls=[OutfallIn("OUT", 5.0, 0.0, 0.0, system="storm_minor")],
            conduits=[C("c", "S1", "OUT", "storm_minor")])
        out, diag = ensure_wastewater_outlet(net, system="sanitary")
        assert diag["n_added"] == 0 and out.outfalls == net.outfalls


class TestSeveralDisconnectedComponents:
    def test_each_stranded_component_gets_its_own_boundary(self):
        """Two separate sanitary basins are two destinations, not one."""
        a = chain(n=3)
        b_j = [J(f"N{i}", 20.0 - i) for i in range(3)]
        net = NetworkIn(
            junctions=list(a.junctions) + b_j,
            outfalls=[],
            conduits=list(a.conduits) + [C(f"n{i}", f"N{i}", f"N{i+1}") for i in range(2)])
        out, diag = ensure_wastewater_outlet(net, system="sanitary")
        assert diag["n_added"] == 2
        assert len({o.name for o in out.outfalls}) == 2


def test_the_result_passes_the_system_integrity_check():
    """The point of the whole exercise: a wastewater network that reaches nothing is an
    error, and this is what stops it being one."""
    from swmmcanada.validate.checks import check_system_outfalls

    assert not check_system_outfalls(chain()).passed
    fixed, _ = ensure_wastewater_outlet(chain(), system="sanitary")
    assert check_system_outfalls(fixed).passed


class TestCompositionWithTheSanitaryGraft:
    """How the pipeline actually uses this: graft the sanitary subgraph, then give it a
    destination. Grafting alone produces a model the validator rejects, which is the
    situation every one of the fleet's sanitary cities is in."""

    def _grafted(self):
        from swmmcanada.sources.cities import base

        storm = NetworkIn(
            junctions=[JunctionIn("S1", 12.0, 0.0, 0.0)],
            outfalls=[OutfallIn("STORM_OUT", 5.0, 0.0, 0.0)],
            conduits=[ConduitIn("sc", "S1", "STORM_OUT", 50.0)])
        sanitary = chain(n=3)
        return base.merge_secondary_system(storm, sanitary, prefix="SAN_",
                                           system="sanitary")

    def test_grafting_alone_leaves_the_model_invalid(self):
        from swmmcanada.validate.checks import check_system_outfalls

        assert not check_system_outfalls(self._grafted()).passed

    def test_adding_the_boundary_makes_it_valid(self):
        from swmmcanada.validate.checks import check_system_outfalls

        fixed, diag = ensure_wastewater_outlet(self._grafted(), system="sanitary")
        assert diag["n_added"] == 1
        assert check_system_outfalls(fixed).passed

    def test_the_boundary_attaches_to_the_prefixed_node_names(self):
        """It runs after the graft, so it must address SAN_-prefixed names."""
        fixed, _ = ensure_wastewater_outlet(self._grafted(), system="sanitary")
        link = next(c for c in fixed.conduits if c.name.endswith("_LINK"))
        assert link.from_node.startswith("SAN_")
