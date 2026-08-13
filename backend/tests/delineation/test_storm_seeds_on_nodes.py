"""Storm subcatchments are seeded on the model's own nodes (municipal practice).

A subcatchment discharges to a node that exists in the model, and in a published pipe
network those nodes are the maintenance holes. Catch basins are surface structures joined
by leads; almost none of them are model nodes, so seeding on them means resolving each one
back to a nearby node anyway — a detour that ends where it started.

Physically the reach between two nodes has ONE tributary area. Three catch basins on that
reach produce three subcatchments discharging to the same node: more model objects, no more
information about the pipe. Per-inlet subdivision belongs to a dual-drainage model, where
inlet capture and street flow are the question being asked.

Measured on downtown Victoria: 541 catch basins against 391 nodes for 88.9 ha.
"""
import pytest

from swmmcanada.delineation import Evidence, resolve
from swmmcanada.validate import schema


class TestSeedChoice:
    def test_a_network_with_nodes_seeds_on_them(self):
        p = resolve(Evidence(n_junctions=391, n_catchbasins=541, n_parcels=3939,
                             dem_available=True, dem_resolution_m=1.0))
        assert p.anchors == "junction", (
            "catch basins are surface structures, not the model's nodes")

    def test_catch_basins_do_not_become_the_unit(self):
        """They remain evidence — which side of a street drains where, and which main a
        lead taps — without becoming the thing land is divided among."""
        p = resolve(Evidence(n_junctions=391, n_catchbasins=541, n_parcels=3939))
        assert p.anchors != "catch_basin"

    def test_inlets_alone_do_not_rescue_a_network_with_no_nodes(self):
        """541 inlets and no nodes still produces nothing: there is no node for a
        subcatchment to discharge to, and inventing one from an inlet would put a surface
        structure into the hydraulic model."""
        p = resolve(Evidence(n_junctions=0, n_catchbasins=541))
        assert p.anchors == "junction" and p.confidence == "low"


class TestStreetSegmentShaping:
    """Each node takes the land draining to its own reach: the street segment plus the lots
    fronting it, back to the rear-lot line. Nearest-POINT assignment carves blocks into a
    triangle fan meeting at the centre, which is nothing a municipality would draw."""

    def test_streets_and_nodes_shape_by_segment(self):
        p = resolve(Evidence(n_junctions=391, n_streets=250, n_parcels=3939))
        assert p.shaping == "street_segment"

    def test_without_streets_it_cannot_split_by_frontage(self):
        p = resolve(Evidence(n_junctions=391, n_parcels=3939))
        assert p.shaping != "street_segment"

    def test_the_method_says_which_it_was(self):
        p = resolve(Evidence(n_junctions=391, n_streets=250, n_parcels=3939))
        assert p.method == schema.METHOD_JUNCTION_STREET

    def test_the_reason_explains_the_unit(self):
        p = resolve(Evidence(n_junctions=391, n_streets=250, n_parcels=3939))
        assert "reach" in p.reason.lower() or "segment" in p.reason.lower()
