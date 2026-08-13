"""Inlets are snapped onto the flow path before they are used as pour points (规划书 §4).

A published inlet coordinate marks the structure, not the pixel water arrives at. A metre or
two of survey offset — or a DEM whose gutter sits half a cell away — puts the pour point on
the kerb or in the carriageway crown instead of the low line. D8 then hands that inlet a
basin of one pixel while its real catchment drains past it to the next one.

The fix is local and bounded: move to the lowest cell within a short search, or don't move.
"""
import numpy as np
import pytest
from affine import Affine

from swmmcanada.network.urban_conditioning import snap_to_local_low

RES = 1.0
TRANSFORM = Affine.translation(0.0, 40.0) * Affine.scale(RES, -RES)


def _gutter_dem(rows=40, cols=40, gutter_row=20):
    """A street cross-section: crown in the middle of the carriageway, gutter along one row."""
    r = np.arange(rows)[:, None]
    return (np.abs(r - gutter_row) * 0.05 + np.zeros((rows, cols))) + 100.0


def _xy(col, row):
    return (col + 0.5) * RES, 40.0 - (row + 0.5) * RES


class TestItFindsTheGutter:
    def test_an_inlet_a_couple_of_metres_off_moves_onto_the_low_line(self):
        dem = _gutter_dem()
        moved, diag = snap_to_local_low({"CB1": _xy(10, 17)}, dem, TRANSFORM,
                                        search_radius_m=5.0)
        _x, y = moved["CB1"]
        row = int((40.0 - y) / RES)
        assert row == 20, f"snapped to row {row}, gutter is row 20"
        assert diag["n_moved"] == 1

    def test_an_inlet_already_on_the_low_line_stays_put(self):
        dem = _gutter_dem()
        start = _xy(10, 20)
        moved, diag = snap_to_local_low({"CB1": start}, dem, TRANSFORM,
                                        search_radius_m=5.0)
        assert moved["CB1"] == pytest.approx(start)
        assert diag["n_moved"] == 0


class TestItRefusesToWander:
    def test_the_search_is_bounded(self):
        """A distant low point is somebody else's gutter. Snapping to it would hand this
        inlet a catchment on the wrong street."""
        dem = _gutter_dem()
        moved, _ = snap_to_local_low({"CB1": _xy(10, 5)}, dem, TRANSFORM,
                                     search_radius_m=3.0)
        _x, y = moved["CB1"]
        assert int((40.0 - y) / RES) != 20, "snapped 15 m to a gutter it does not serve"

    def test_the_distance_moved_is_reported(self):
        dem = _gutter_dem()
        _m, diag = snap_to_local_low({"CB1": _xy(10, 17)}, dem, TRANSFORM,
                                     search_radius_m=5.0)
        assert diag["max_move_m"] > 0
        assert diag["search_radius_m"] == 5.0


class TestDegenerate:
    def test_an_inlet_outside_the_dem_is_left_alone(self):
        dem = _gutter_dem()
        far = (1000.0, 1000.0)
        moved, diag = snap_to_local_low({"CB1": far}, dem, TRANSFORM, search_radius_m=5.0)
        assert moved["CB1"] == far
        assert diag["n_outside_dem"] == 1

    def test_nodata_around_an_inlet_does_not_pull_it_into_a_hole(self):
        dem = _gutter_dem()
        dem[20, :] = np.nan                       # the gutter row is a nodata seam
        moved, _ = snap_to_local_low({"CB1": _xy(10, 17)}, dem, TRANSFORM,
                                     search_radius_m=5.0)
        _x, y = moved["CB1"]
        assert int((40.0 - y) / RES) != 20, "snapped into a nodata seam"

    def test_no_inlets_is_not_an_error(self):
        moved, diag = snap_to_local_low({}, _gutter_dem(), TRANSFORM, search_radius_m=5.0)
        assert moved == {} and diag["n_moved"] == 0


def test_no_inlet_moves_further_than_the_stated_radius():
    """The window is square, so its diagonal reaches further than its side. A move of
    6.3 m under a stated 4 m radius was measured on live Victoria — the number a reader
    is given has to bound what actually happened."""
    dem = _gutter_dem()
    starts = {f"CB{c}": _xy(c, 17) for c in range(5, 35)}
    moved, diag = snap_to_local_low(dem=dem, points=starts, transform=TRANSFORM,
                                    search_radius_m=4.0)
    for name, (x, y) in moved.items():
        x0, y0 = starts[name]
        assert ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5 <= 4.0 + 1e-9, name
    assert diag["max_move_m"] <= 4.0
