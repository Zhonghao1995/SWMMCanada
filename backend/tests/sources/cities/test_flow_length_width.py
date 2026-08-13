"""Subcatchment width comes from a flow length, not from assuming a square (规划书 §5).

SWMM's width is area divided by the distance water travels overland. `sqrt(area)` is the
answer for a square cell and only for a square cell. Municipal cells are street-frontage
strips: water crosses the short way to the gutter, so a strip is much wider than its area's
square root, and using the square root makes it route as if it were four times longer than
it is.
"""
import math

import pytest

from swmmcanada.sources.cities.base import characteristic_flow_length_m

# A metric-CRS square, 100 m on a side (1 ha), with its inlet at the centre.
SQUARE = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
SQUARE_SEED = (50.0, 50.0)

# The same area as a street strip: 400 m long, 25 m deep, inlet on the long edge.
STRIP = [(0.0, 0.0), (400.0, 0.0), (400.0, 25.0), (0.0, 25.0), (0.0, 0.0)]
STRIP_SEED = (200.0, 0.0)


def _poly(ring):
    from shapely.geometry import Polygon

    return Polygon(ring)


def test_a_square_cell_flows_about_its_own_side_length():
    """The square is the case `sqrt(area)` was right for; the new estimate must not move it
    far, or every existing model shifts for no reason."""
    L = characteristic_flow_length_m(_poly(SQUARE), SQUARE_SEED)
    assert 0.3 * 100.0 < L < 1.2 * 100.0, L


def test_a_street_strip_flows_much_less_than_the_square_root_of_its_area():
    """1 ha as a 400x25 strip: water crosses 25 m to the gutter, not 100 m."""
    L = characteristic_flow_length_m(_poly(STRIP), STRIP_SEED)
    assert L < 0.5 * math.sqrt(10000.0), f"flow length {L:.1f} m is barely shorter than 100 m"


def test_width_from_flow_length_makes_a_strip_wider_than_sqrt_area():
    """The consequence that matters: area / flow_length, not sqrt(area)."""
    area = 10000.0
    L = characteristic_flow_length_m(_poly(STRIP), STRIP_SEED)
    assert area / L > 2.0 * math.sqrt(area)


def test_a_degenerate_cell_falls_back_rather_than_dividing_by_zero():
    L = characteristic_flow_length_m(_poly([(0.0, 0.0), (1e-9, 0.0), (0.0, 1e-9),
                                            (0.0, 0.0)]), (0.0, 0.0))
    assert L > 0


class TestItReachesTheSubcatchments:
    """A helper nothing calls changes no model."""

    def test_the_city_path_no_longer_assumes_a_square(self):
        import inspect

        from swmmcanada.sources.cities import base

        src = inspect.getsource(base.delineate_catchbasin_subcatchments)
        assert "math.sqrt(area_m2)" not in src, "width still assumes a square cell"
        assert "characteristic_flow_length_m" in src

    def test_width_is_reported_so_a_reader_knows_which_estimate_produced_it(self):
        import inspect

        from swmmcanada.sources.cities import base

        src = inspect.getsource(base.delineate_catchbasin_subcatchments)
        assert "width_method" in src
