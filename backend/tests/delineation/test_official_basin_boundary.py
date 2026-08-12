"""Official basins bound our cells, they do not become them (规划书 §4, ADR 0029 Q2).

Phase 0 measured every published catchment layer in the fleet as macro — one per outfall or
pump station, tens of hectares each. Too coarse to be model units, but they carry a fact we
cannot derive: the city knows which land drains to which outfall.

So they are used as a hard edge. A cell we drew may sit inside one, but it may not straddle
two — crossing that line means routing land to an outfall the city says it does not reach.
"""
import pytest
from shapely.geometry import Polygon, box

from swmmcanada.delineation.boundary import clip_to_official_basins

# Two basins meeting at x = 0.
WEST = box(-100.0, -100.0, 0.0, 100.0)
EAST = box(0.0, -100.0, 100.0, 100.0)


def _basin(poly, outlet):
    return {"geometry": poly, "outlet": outlet}


class _Cell:
    """The shape the shaping seam hands on: an id, a metric polygon, its seed."""

    def __init__(self, name, poly, seed):
        self.name, self.polygon, self.seed = name, poly, seed


class TestItTruncatesStraddlingCells:
    def test_a_cell_crossing_the_divide_is_cut_back_to_its_own_basin(self):
        straddling = box(-20.0, -10.0, 20.0, 10.0)          # 40 x 20, half either side
        cells = [_Cell("S1", straddling, (-10.0, 0.0)),     # seeded in the WEST basin
                 _Cell("S2", box(20.0, -10.0, 60.0, 10.0), (40.0, 0.0))]   # takes the cut half
        out, diag = clip_to_official_basins(cells, [_basin(WEST, "OUT_W"),
                                                    _basin(EAST, "OUT_E")])
        assert out[0].polygon.bounds[2] == pytest.approx(0.0), "still reaches into the east"
        assert diag["n_clipped"] == 1

    def test_a_cell_wholly_inside_one_basin_is_untouched(self):
        inside = box(-50.0, -10.0, -30.0, 10.0)
        cells = [_Cell("S1", inside, (-40.0, 0.0))]
        out, diag = clip_to_official_basins(cells, [_basin(WEST, "OUT_W"),
                                                    _basin(EAST, "OUT_E")])
        assert out[0].polygon.equals(inside)
        assert diag["n_clipped"] == 0

    def test_area_lost_to_the_cut_is_reported(self):
        """Land removed from a cell has to be accounted for, or coverage silently drops."""
        straddling = box(-20.0, -10.0, 20.0, 10.0)
        cells = [_Cell("S1", straddling, (-10.0, 0.0)),
                 _Cell("S2", box(20.0, -10.0, 60.0, 10.0), (40.0, 0.0))]
        _out, diag = clip_to_official_basins(cells, [_basin(WEST, "OUT_W"),
                                                     _basin(EAST, "OUT_E")])
        assert diag["area_removed_m2"] == pytest.approx(400.0, rel=0.01)


class TestItRefusesToActOnGuesswork:
    def test_a_cell_whose_seed_is_in_no_basin_is_left_alone(self):
        """Outside the published coverage the yardstick does not reach, and cutting to a
        basin the seed is not in would invent a routing decision."""
        outside = box(200.0, 200.0, 220.0, 220.0)
        cells = [_Cell("S1", outside, (210.0, 210.0))]
        out, diag = clip_to_official_basins(cells, [_basin(WEST, "OUT_W")])
        assert out[0].polygon.equals(outside)
        assert diag["n_outside_official"] == 1

    def test_no_official_layer_is_a_no_op(self):
        cells = [_Cell("S1", box(-20.0, -10.0, 20.0, 10.0), (-10.0, 0.0))]
        out, diag = clip_to_official_basins(cells, [])
        assert out[0].polygon.equals(cells[0].polygon)
        assert diag["applied"] is False

    def test_a_cut_that_would_erase_a_cell_is_declined(self):
        """Clipping must not delete land from the model. A cell that survives as a sliver is
        a sign the seed and the basins disagree, and dropping it silently loses coverage."""
        straddling = box(-1.0, -10.0, 40.0, 10.0)   # only 1 m of it is in WEST
        cells = [_Cell("S1", straddling, (-0.5, 0.0))]
        out, diag = clip_to_official_basins(cells, [_basin(WEST, "OUT_W"),
                                                    _basin(EAST, "OUT_E")],
                                            min_retained_frac=0.2)
        assert out[0].polygon.equals(straddling)
        assert diag["n_declined_too_small"] == 1


class TestClippingRedistributesRatherThanDeletes:
    """A boundary says which node land belongs to, not that it stops raining there.

    Cutting a straddling cell back to its own basin removed the far half from the model
    entirely. Measured on a live downtown: coverage 100% before the clip and 93% after —
    6.1 ha of land producing no runoff, and the delineation that fed it was complete.
    """

    def test_the_far_half_goes_to_a_cell_in_that_basin(self):
        straddling = box(-20.0, -10.0, 20.0, 10.0)          # half either side of x=0
        neighbour = box(20.0, -10.0, 60.0, 10.0)            # wholly in EAST
        cells = [_Cell("S1", straddling, (-10.0, 0.0)),
                 _Cell("S2", neighbour, (40.0, 0.0))]
        out, diag = clip_to_official_basins(cells, [_basin(WEST, "OUT_W"),
                                                    _basin(EAST, "OUT_E")])
        by_name = {c.name: c for c in out}
        assert by_name["S1"].polygon.bounds[2] == pytest.approx(0.0)
        assert by_name["S2"].polygon.area > neighbour.area, (
            "the piece cut from S1 was deleted rather than handed to its basin's cell")
        assert diag["area_reassigned_m2"] == pytest.approx(400.0, rel=0.05)

    def test_total_area_is_preserved(self):
        straddling = box(-20.0, -10.0, 20.0, 10.0)
        neighbour = box(20.0, -10.0, 60.0, 10.0)
        cells = [_Cell("S1", straddling, (-10.0, 0.0)),
                 _Cell("S2", neighbour, (40.0, 0.0))]
        before = sum(c.polygon.area for c in cells)
        out, _ = clip_to_official_basins(cells, [_basin(WEST, "OUT_W"),
                                                 _basin(EAST, "OUT_E")])
        assert sum(c.polygon.area for c in out) == pytest.approx(before, rel=0.01)

    def test_with_nowhere_to_send_it_the_cell_keeps_it(self):
        """No cell serves that basin, so cutting would delete the land. The boundary is
        worth less than the water."""
        straddling = box(-20.0, -10.0, 20.0, 10.0)
        cells = [_Cell("S1", straddling, (-10.0, 0.0))]
        out, diag = clip_to_official_basins(cells, [_basin(WEST, "OUT_W"),
                                                    _basin(EAST, "OUT_E")])
        assert out[0].polygon.area == pytest.approx(straddling.area)
        assert diag["n_declined_nowhere_to_send"] == 1
