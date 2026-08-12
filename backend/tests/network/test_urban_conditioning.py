"""Urban DEM conditioning: kerbs are walls with gates (规划书 §4, priority 2).

A bare DEM does not know that a 150 mm kerb decides where street runoff goes. At 1 m LiDAR
posting the kerb is often invisible — one pixel of a smooth cross-slope — so D8 sends water
across it into the front garden, when in reality it runs along the gutter to the nearest
inlet.

Conditioning states the three facts the terrain omits: a kerb is a barrier, a kerb drop or
inlet is the one place through it, and a building is not crossed at all.
"""
import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon

from swmmcanada.network.urban_conditioning import UrbanConditioningConfig, condition_urban_dem

PX = 1.0
TRANSFORM_ORIGIN = (0.0, 20.0)   # upper-left corner, north-up


def _transform():
    from affine import Affine

    return Affine.translation(*TRANSFORM_ORIGIN) * Affine.scale(PX, -PX)


def _flat_dem(rows=20, cols=20, z=10.0):
    return np.full((rows, cols), z, dtype="float64")


def _vertical_kerb(x=10.0, y0=0.0, y1=20.0):
    return LineString([(x, y0), (x, y1)])


def test_a_kerb_is_raised_into_a_barrier():
    dem = _flat_dem()
    out, diag = condition_urban_dem(dem, _transform(), kerbs=[_vertical_kerb()])
    assert out.max() > dem.max(), "the kerb left no ridge in the surface"
    assert diag["n_kerb_cells"] > 0


def test_a_kerb_drop_leaves_a_gate_through_the_barrier():
    """The whole point: water crosses at the opening and nowhere else."""
    cfg = UrbanConditioningConfig()
    out, _ = condition_urban_dem(_flat_dem(), _transform(), kerbs=[_vertical_kerb()],
                                 openings=[Point(10.0, 10.0)])
    ridge = out[:, 10]
    gate = ridge.min()
    wall = ridge.max()
    assert wall - gate >= cfg.kerb_height_m * 0.5, (
        "the opening is no lower than the kerb either side of it")


def test_without_an_opening_the_barrier_is_unbroken():
    out, _ = condition_urban_dem(_flat_dem(), _transform(), kerbs=[_vertical_kerb()])
    ridge = out[:, 10]
    assert ridge.min() == pytest.approx(ridge.max(), abs=1e-9)


def test_a_building_is_raised_far_enough_not_to_be_crossed():
    dem = _flat_dem()
    out, diag = condition_urban_dem(dem, _transform(),
                                    buildings=[Polygon([(2, 2), (6, 2), (6, 6), (2, 6)])])
    assert out[15:18, 3:5].max() > dem.max() + UrbanConditioningConfig().kerb_height_m
    assert diag["n_building_cells"] > 0


def test_nothing_to_condition_returns_the_surface_untouched():
    dem = _flat_dem()
    out, diag = condition_urban_dem(dem, _transform())
    assert np.array_equal(out, dem)
    assert diag["applied"] is False


def test_the_original_surface_is_not_mutated():
    """Conditioning is one option among several; the caller may still need the raw DEM."""
    dem = _flat_dem()
    before = dem.copy()
    condition_urban_dem(dem, _transform(), kerbs=[_vertical_kerb()])
    assert np.array_equal(dem, before)


def test_nodata_stays_nodata():
    dem = _flat_dem()
    dem[0, :] = np.nan
    out, _ = condition_urban_dem(dem, _transform(), kerbs=[_vertical_kerb()])
    assert np.isnan(out[0, 0]), "conditioning invented ground where there was none"
