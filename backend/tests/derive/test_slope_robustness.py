"""Cell slope is the typical slope, not the average of one (规划书 §5).

A mean over pixels lets a single artefact set a whole cell's slope. DEM windows in cities
are full of them: a building edge, a bridge deck, a retaining wall, a nodata seam. One
40-metre step across one pixel pair pulls a flat downtown block into hillside territory, and
SWMM's overland routing takes that at face value.
"""
import numpy as np
import pytest

from swmmcanada.derive.core import _slope_pct_from_grid


PX = 10.0  # metres


def _plane(rows, cols, rise_per_m):
    """A uniform slope: every pixel pair has the same gradient."""
    return np.fromfunction(lambda r, c: c * PX * rise_per_m, (rows, cols), dtype="float64")


def test_a_uniform_slope_reads_as_itself():
    grid = _plane(12, 12, 0.02)          # 2 %
    assert _slope_pct_from_grid(grid, PX, PX) == pytest.approx(2.0, abs=0.1)


def test_one_artefact_does_not_set_the_cell_slope():
    """A retaining wall in the corner of an otherwise flat block."""
    grid = _plane(12, 12, 0.01)          # 1 % everywhere
    grid[5, 5] += 40.0                   # one 40 m step
    assert _slope_pct_from_grid(grid, PX, PX) == pytest.approx(1.0, abs=0.5), (
        "one pixel is deciding the slope of the whole cell")


def test_a_genuinely_steep_cell_is_still_steep():
    """Robustness must not flatten real terrain — the gate that picks DEM delineation over
    Voronoi reads these numbers."""
    assert _slope_pct_from_grid(_plane(12, 12, 0.13), PX, PX) > 10.0


def test_nodata_holes_are_ignored_rather_than_counted_as_flat():
    grid = _plane(12, 12, 0.03)
    grid[2:5, 2:5] = np.nan
    assert _slope_pct_from_grid(grid, PX, PX) == pytest.approx(3.0, abs=0.3)


def test_too_small_a_window_reports_nothing_rather_than_guessing():
    assert _slope_pct_from_grid(np.array([[1.0]]), PX, PX) is None
    assert _slope_pct_from_grid(np.full((6, 6), np.nan), PX, PX) is None
