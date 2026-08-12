"""System integrity (ADR 0029 Q5, superseding ADR 0011's stricter rule).

ADR 0011 required every tagged system to be isolated and to own an outfall. That held only
while sanitary was a disconnected tracer; it is false for combined sewers, where storm and
sanitary meet by design. Ottawa serves storm, sanitary and combined pipes from one network
and the old rule would have failed the city for describing itself accurately.

The rule was ERROR-level and build-blocking and had no tests at all. These are they.
"""
from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn
from swmmcanada.validate.checks import check_system_outfalls


def net(junctions, outfalls, conduits):
    return NetworkIn(junctions=junctions, outfalls=outfalls, conduits=conduits)


def J(name, system="storm_minor"):
    return JunctionIn(name, 10.0, 0.0, 0.0, system=system)


def O(name, system="storm_minor"):
    return OutfallIn(name, 8.0, 0.0, 0.0, system=system)


def C(name, a, b, system="storm_minor"):
    return ConduitIn(name, a, b, 50.0, system=system)


class TestCombinedIsTheLegitimateInterface:
    def test_storm_may_join_combined(self):
        r = check_system_outfalls(net(
            [J("S1"), J("K1", "combined")],
            [O("OUT", "combined")],
            [C("c1", "S1", "K1", "combined"), C("c2", "K1", "OUT", "combined")]))
        assert r.passed, r.details

    def test_sanitary_may_join_combined(self):
        r = check_system_outfalls(net(
            [J("N1", "sanitary"), J("K1", "combined")],
            [O("OUT", "combined")],
            [C("c1", "N1", "K1", "combined"), C("c2", "K1", "OUT", "combined")]))
        assert r.passed, r.details

    def test_all_three_in_one_network_is_fine(self):
        """Ottawa's actual shape: storm, sanitary and combined from one service."""
        r = check_system_outfalls(net(
            [J("S1"), J("N1", "sanitary"), J("K1", "combined")],
            [O("OUT", "combined")],
            [C("c1", "S1", "K1", "combined"), C("c2", "N1", "K1", "combined"),
             C("c3", "K1", "OUT", "combined")]))
        assert r.passed, r.details


class TestTheHardRuleSurvives:
    def test_a_direct_storm_sanitary_link_is_an_error(self):
        """No combined pipe between them and no published topology saying otherwise: this
        is a cross-connection, the fault municipalities run programmes to find."""
        r = check_system_outfalls(net(
            [J("S1"), J("N1", "sanitary")],
            [O("OUT")],
            [C("bad", "S1", "N1"), C("c2", "S1", "OUT")]))
        assert not r.passed
        assert "no combined pipe between them" in r.metrics["sample"][0]

    def test_the_offending_conduit_is_named(self):
        r = check_system_outfalls(net(
            [J("S1"), J("N1", "sanitary")], [O("OUT")],
            [C("crossconn_7", "S1", "N1"), C("c2", "S1", "OUT")]))
        assert "crossconn_7" in r.metrics["sample"][0]


class TestOutfallReachabilityIsPerComponent:
    def test_one_outfall_may_serve_a_mixed_component(self):
        """Requiring each system to own an outfall would demand a storm outfall for a
        combined network whose water leaves through an interceptor."""
        r = check_system_outfalls(net(
            [J("S1"), J("N1", "sanitary"), J("K1", "combined")],
            [O("INTERCEPTOR", "combined")],
            [C("c1", "S1", "K1", "combined"), C("c2", "N1", "K1", "combined"),
             C("c3", "K1", "INTERCEPTOR", "combined")]))
        assert r.passed, r.details

    def test_a_component_with_no_outfall_is_an_error(self):
        r = check_system_outfalls(net(
            [J("S1"), J("S2"), J("ORPHAN1"), J("ORPHAN2")],
            [O("OUT")],
            [C("c1", "S1", "OUT"), C("c2", "S1", "S2"),
             C("c3", "ORPHAN1", "ORPHAN2")]))
        assert not r.passed
        assert any("reaches no outfall" in p for p in r.metrics["sample"])

    def test_the_orphan_report_names_its_systems_and_nodes(self):
        r = check_system_outfalls(net(
            [J("S1"), J("A", "sanitary"), J("B", "sanitary")],
            [O("OUT")],
            [C("c1", "S1", "OUT"), C("c2", "A", "B", "sanitary")]))
        problem = next(p for p in r.metrics["sample"] if "reaches no outfall" in p)
        assert "sanitary" in problem and ("A" in problem or "B" in problem)

    def test_separated_systems_each_with_their_own_outfall_still_pass(self):
        """The ADR 0011 shape must keep working — this loosens the rule, not replaces it."""
        r = check_system_outfalls(net(
            [J("S1"), J("N1", "sanitary")],
            [O("STORM_OUT"), O("SAN_WWTP", "sanitary")],
            [C("c1", "S1", "STORM_OUT"), C("c2", "N1", "SAN_WWTP", "sanitary")]))
        assert r.passed, r.details


class TestDegenerate:
    def test_an_empty_network_has_no_problems_to_report(self):
        assert check_system_outfalls(net([], [], [])).passed

    def test_the_check_is_error_severity(self):
        from swmmcanada.validate import schema
        assert check_system_outfalls(net([], [], [])).severity == schema.ERROR
