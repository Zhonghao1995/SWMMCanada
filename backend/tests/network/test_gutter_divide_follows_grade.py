"""The divide between two nodes sits at the crest, not at the midpoint (规划书 §4).

Water in a gutter runs downhill to whichever node is lower, not to whichever is nearer. On a
street that crests off-centre — the common case, since maintenance holes are placed for pipe
runs and not for symmetry — a geometric midpoint divide sends a third of the block to the
wrong node, and the pipe sized from it is wrong by the same third.

The terrain no longer cuts cells (that produced slivers). It decides which way the gutter
runs inside a cell that street frontage already shaped.
"""
import networkx as nx
import pytest

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.service_area import edge_split_cells

AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.3730, 48.4240], [-123.3630, 48.4240], [-123.3630, 48.4290],
    [-123.3730, 48.4290], [-123.3730, 48.4240]]]})

WEST, EAST = -123.3710, -123.3650
LAT = 48.4265
NODES = {"MH_W": (WEST + 0.0002, LAT), "MH_E": (EAST - 0.0002, LAT)}


def _street():
    g = nx.Graph()
    g.add_node("a", x=WEST, y=LAT)
    g.add_node("b", x=EAST, y=LAT)
    g.add_edge("a", "b")
    return g


def _crest_at(fraction):
    """Ground that rises to a crest `fraction` of the way from west to east."""
    def z(lon, lat):
        t = (lon - WEST) / (EAST - WEST)
        return 100.0 - abs(t - fraction) * 10.0
    return z


def _share_west(cells):
    w = cells["MH_W"].area_m2 if "MH_W" in cells else 0.0
    e = cells["MH_E"].area_m2 if "MH_E" in cells else 0.0
    return w / (w + e) if (w + e) else 0.0


class TestTheCrestDecides:
    def test_a_crest_east_of_centre_gives_the_west_node_more(self):
        cells = edge_split_cells(_street(), NODES, AOI.geometry, AOI,
                                 elevation=_crest_at(0.75))
        assert _share_west(cells) > 0.6, (
            "the gutter crests three quarters east, so most of it drains west")

    def test_a_crest_west_of_centre_gives_the_east_node_more(self):
        cells = edge_split_cells(_street(), NODES, AOI.geometry, AOI,
                                 elevation=_crest_at(0.25))
        assert _share_west(cells) < 0.4

    def test_a_centred_crest_splits_evenly(self):
        cells = edge_split_cells(_street(), NODES, AOI.geometry, AOI,
                                 elevation=_crest_at(0.5))
        assert 0.4 < _share_west(cells) < 0.6


class TestWithoutTerrainNothingChanges:
    def test_no_elevation_keeps_the_midpoint_divide(self):
        """Most of the fleet builds without a usable surface, and their delineation must be
        exactly what it was."""
        plain = edge_split_cells(_street(), NODES, AOI.geometry, AOI)
        assert 0.4 < _share_west(plain) < 0.6

    def test_a_failing_elevation_lookup_falls_back_rather_than_failing(self):
        def boom(lon, lat):
            raise RuntimeError("no data here")

        cells = edge_split_cells(_street(), NODES, AOI.geometry, AOI, elevation=boom)
        assert cells, "a broken surface must cost the refinement, not the delineation"


class TestItReachesTheBuild:
    """A refinement nothing passes ground to is the same defect as a branch nothing
    executes — and that has happened four times on this branch already."""

    def test_the_delineator_forwards_ground_to_the_split(self):
        import inspect

        from swmmcanada.network import delineate_dem

        src = inspect.getsource(delineate_dem.delineate_junction_subcatchments)
        call = src[src.index("edge_split_cells("):]
        call = call[:call.index(")") + 1]
        assert "elevation" in call, f"the split is called without ground: {call}"


class TestInletsInterceptTheGutter:
    """Grade alone sends a whole falling street to its lowest node, leaving every node above
    it dry. That is not what happens: inlets exist precisely so water does not run the length
    of a block, and each stretch of gutter is caught by the first inlet below it.

    This is catch basins doing the job they are kept for — evidence about where surface water
    enters — without becoming the unit land is divided among. Several inlets on one reach
    resolve to the same node and merge.
    """

    def _falling_street_elevation(self):
        def z(lon, lat):
            t = (lon - WEST) / (EAST - WEST)
            return 100.0 - t * 5.0            # falls the whole way, west to east
        return z

    def test_without_inlets_a_falling_street_all_goes_downhill(self):
        cells = edge_split_cells(_street(), NODES, AOI.geometry, AOI,
                                 elevation=self._falling_street_elevation())
        assert _share_west(cells) < 0.1, "premise: grade alone consolidates downhill"

    def test_an_inlet_partway_along_keeps_the_upper_stretch_upstream(self):
        """The inlet sits clearly nearer the west node — at the midpoint it is equidistant
        and which main it taps is genuinely ambiguous, which is a property of the street and
        not something the split should pretend to resolve."""
        upper = WEST + (EAST - WEST) * 0.3
        cells = edge_split_cells(
            _street(), NODES, AOI.geometry, AOI,
            elevation=self._falling_street_elevation(),
            inlets=[(upper, LAT)])
        assert _share_west(cells) > 0.25, (
            "the inlet catches the stretch above it; it must not run past to the far node")

    def test_inlets_resolve_to_nodes_rather_than_becoming_them(self):
        """Two inlets nearer the same node produce one cell, not two."""
        cells = edge_split_cells(
            _street(), NODES, AOI.geometry, AOI,
            elevation=self._falling_street_elevation(),
            inlets=[(WEST + (EAST - WEST) * 0.55, LAT),
                    (WEST + (EAST - WEST) * 0.60, LAT)])
        assert set(cells) <= set(NODES)

    def test_no_inlets_published_changes_nothing(self):
        plain = edge_split_cells(_street(), NODES, AOI.geometry, AOI,
                                 elevation=self._falling_street_elevation())
        same = edge_split_cells(_street(), NODES, AOI.geometry, AOI,
                                elevation=self._falling_street_elevation(), inlets=[])
        assert _share_west(plain) == pytest.approx(_share_west(same))
