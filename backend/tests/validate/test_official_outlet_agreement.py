"""Official-outlet agreement (ADR 0029 Q2, #129).

Municipal catchment polygons supply **boundaries**; we resolve outlets ourselves, because
the outlet field lives in the city's own id space and half the fleet infers topology
geometrically, so it cannot be joined. The city's declaration is not discarded though — it
becomes the yardstick.

The metric: for each unit we produced, trace downstream to the outfall it actually reaches,
and compare that against the outfall the polygon covering it declares. The rate is a
standing acceptance number alongside topology agreement, and it is what promotes a Level 1
candidate or demotes it (ADR 0030: Level 1 is two beats and revocable).
"""
import pytest

from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     SurfaceCatchment)
from swmmcanada.validate.outlet_agreement import official_outlet_agreement

RING_W = [(-123.370, 48.420), (-123.365, 48.420), (-123.365, 48.430), (-123.370, 48.430),
          (-123.370, 48.420)]
RING_E = [(-123.365, 48.420), (-123.360, 48.420), (-123.360, 48.430), (-123.365, 48.430),
          (-123.365, 48.420)]


def _network():
    """Two branches: J1 -> OUT_WEST, J2 -> OUT_EAST."""
    return NetworkIn(
        junctions=[JunctionIn("J1", 9.0, -123.3675, 48.425),
                   JunctionIn("J2", 9.0, -123.3625, 48.425)],
        outfalls=[OutfallIn("OUT_WEST", 5.0, -123.3695, 48.425),
                  OutfallIn("OUT_EAST", 5.0, -123.3605, 48.425)],
        conduits=[ConduitIn("C1", "J1", "OUT_WEST", 100.0),
                  ConduitIn("C2", "J2", "OUT_EAST", 100.0)])


def _official(ring, declared_outlet):
    return {"type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]},
            "properties": {"NAME": "S60", "OUTLET": declared_outlet}}


def _unit(name, outlet_node, ring):
    return SurfaceCatchment(name, outlet_node, 1.0, 50.0, 100.0, 1.0, polygon=ring)


class TestAgreement:
    def test_a_unit_draining_where_the_city_says_agrees(self):
        rate, diag = official_outlet_agreement(
            [_unit("S1", "J1", RING_W)], _network(),
            [_official(RING_W, "OUT_WEST")], outlet_field="OUTLET")
        assert rate == 1.0 and diag["n_agree"] == 1

    def test_a_unit_draining_elsewhere_disagrees(self):
        rate, diag = official_outlet_agreement(
            [_unit("S1", "J2", RING_W)], _network(),
            [_official(RING_W, "OUT_WEST")], outlet_field="OUTLET")
        assert rate == 0.0
        assert diag["disagreements"][0]["ours"] == "OUT_EAST"
        assert diag["disagreements"][0]["declared"] == "OUT_WEST"

    def test_the_rate_is_a_fraction_of_comparable_units(self):
        units = [_unit("S1", "J1", RING_W), _unit("S2", "J2", RING_E),
                 _unit("S3", "J2", RING_W)]
        officials = [_official(RING_W, "OUT_WEST"), _official(RING_E, "OUT_EAST")]
        rate, diag = official_outlet_agreement(units, _network(), officials,
                                               outlet_field="OUTLET")
        assert diag["n_comparable"] == 3
        assert rate == pytest.approx(2 / 3)


class TestWhatCannotBeCompared:
    def test_units_outside_every_official_polygon_are_excluded_not_counted_wrong(self):
        """An area the city drew no catchment for is not a disagreement — it is a place the
        yardstick does not reach, and folding it in would depress a real measurement."""
        far = [(-120.0, 40.0), (-119.9, 40.0), (-119.9, 40.1), (-120.0, 40.1),
               (-120.0, 40.0)]
        rate, diag = official_outlet_agreement(
            [_unit("S1", "J1", RING_W), _unit("S2", "J1", far)], _network(),
            [_official(RING_W, "OUT_WEST")], outlet_field="OUTLET")
        assert diag["n_comparable"] == 1 and diag["n_outside_official"] == 1
        assert rate == 1.0

    def test_a_polygon_declaring_an_outlet_we_do_not_have_is_reported(self):
        """The city names an outfall absent from our extract — an AOI edge effect, not a
        routing error, and it must not be scored as one."""
        _, diag = official_outlet_agreement(
            [_unit("S1", "J1", RING_W)], _network(),
            [_official(RING_W, "DOF001849")], outlet_field="OUTLET")
        assert diag["n_declared_outlet_unknown"] == 1
        assert diag["n_comparable"] == 0

    def test_no_official_layer_yields_no_rate_rather_than_a_perfect_score(self):
        rate, diag = official_outlet_agreement([_unit("S1", "J1", RING_W)], _network(), [])
        assert rate is None and diag["reason"]

    def test_units_without_geometry_cannot_be_placed(self):
        bare = SurfaceCatchment("S1", "J1", 1.0, 50.0, 100.0, 1.0)
        _, diag = official_outlet_agreement([bare], _network(),
                                            [_official(RING_W, "OUT_WEST")],
                                            outlet_field="OUTLET")
        assert diag["n_no_geometry"] == 1


class TestDownstreamTracing:
    def test_it_follows_the_chain_not_just_the_first_hop(self):
        """A unit's outlet node is rarely the outfall; agreement is about where the water
        ends up."""
        net = NetworkIn(
            junctions=[JunctionIn("J1", 9.0, -123.3675, 48.425),
                       JunctionIn("J9", 8.0, -123.368, 48.425)],
            outfalls=[OutfallIn("OUT_WEST", 5.0, -123.3695, 48.425)],
            conduits=[ConduitIn("C1", "J1", "J9", 50.0),
                      ConduitIn("C2", "J9", "OUT_WEST", 50.0)])
        rate, _ = official_outlet_agreement([_unit("S1", "J1", RING_W)], net,
                                            [_official(RING_W, "OUT_WEST")],
                                            outlet_field="OUTLET")
        assert rate == 1.0

    def test_a_unit_that_reaches_no_outfall_is_reported_separately(self):
        net = NetworkIn(junctions=[JunctionIn("J1", 9.0, -123.3675, 48.425)],
                        outfalls=[], conduits=[])
        _, diag = official_outlet_agreement([_unit("S1", "J1", RING_W)], net,
                                            [_official(RING_W, "OUT_WEST")],
                                            outlet_field="OUTLET")
        assert diag["n_reaches_no_outfall"] == 1
        assert diag["n_comparable"] == 0
