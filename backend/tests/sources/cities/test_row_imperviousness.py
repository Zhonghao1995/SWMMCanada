"""Not all of a road reserve is pavement (规划书 §5).

Imperviousness counted every square metre outside a parcel as impervious road. Downtown
that is close enough — carriageway plus sidewalk fills the reserve. In a suburb it is not:
a 20 m reserve carries an 8 m carriageway between grass boulevards, and calling the whole
thing pavement inflates a residential cell by a third.

The assumption was implicit at 1.0 and undocumented, which is the part worth fixing first:
a stated 0.85 can be argued with, a silent 1.0 cannot.
"""
import pytest
from shapely.geometry import box

from swmmcanada.sources.cities.base import (CatchbasinSubcatchmentConfig,
                                            _impervious_fraction)


def _gdf(geoms):
    import geopandas as gpd

    g = gpd.GeoDataFrame(geometry=gpd.GeoSeries(geoms))
    return g, (g.sindex if len(g) else None)


CELL = box(0, 0, 100, 100)                       # 1 ha
PARCELS = [box(0, 0, 100, 60)]                   # 60% parcelled, 40% road reserve
ROOFS = [box(10, 10, 40, 40)]                    # 900 m2 of roof


def _pct(config):
    par, par_ix = _gdf(PARCELS)
    bld, bld_ix = _gdf(ROOFS)
    pct, based = _impervious_fraction(CELL, par, par_ix, bld, bld_ix, config=config)
    assert based, "the cell has parcels and roof evidence"
    return pct


def test_the_reserve_is_not_assumed_to_be_all_pavement():
    """Default: 900 m2 roof + 85% of the 4,000 m2 reserve = 4,300 m2 of 10,000."""
    assert _pct(CatchbasinSubcatchmentConfig()) == pytest.approx(43.0, abs=1.0)


def test_the_share_is_configurable_and_1_0_reproduces_the_old_answer():
    old = _pct(CatchbasinSubcatchmentConfig(road_reserve_impervious_frac=1.0))
    assert old == pytest.approx(49.0, abs=1.0)


def test_a_fully_paved_reserve_can_still_be_declared():
    """Downtown cities where the reserve really is kerb-to-kerb pavement plus sidewalk."""
    assert _pct(CatchbasinSubcatchmentConfig(road_reserve_impervious_frac=1.0)) > _pct(
        CatchbasinSubcatchmentConfig())


def test_roofs_are_never_discounted():
    """The allowance applies to the reserve, not to buildings — a roof is a roof."""
    cfg = CatchbasinSubcatchmentConfig(road_reserve_impervious_frac=0.0)
    assert _pct(cfg) == pytest.approx(9.0, abs=0.5)
