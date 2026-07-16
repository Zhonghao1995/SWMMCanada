"""Physical imperviousness (ADR 0023 cut 1): roofs+roads replace the land-cover mean only
where buildings are actually mapped."""
import networkx as nx
import pytest
from shapely.geometry import Polygon

from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.derive.physical import refine_imperviousness
from swmmcanada.geo import aoi_from_geojson

# ~111 m x 111 m cell at the equator-ish scale of our test AOI
CELL = [(-123.5100, 48.4400), (-123.5086, 48.4400), (-123.5086, 48.4409),
        (-123.5100, 48.4409), (-123.5100, 48.4400)]
AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.512, 48.439], [-123.507, 48.439], [-123.507, 48.442],
    [-123.512, 48.442], [-123.512, 48.439]]]})


def _sub(imperv=70.0):
    return SubcatchmentIn(name="S1", outlet_node="J1", area_ha=1.1, pct_imperv=imperv,
                          width_m=100.0, pct_slope=1.0, polygon=CELL)


def _roof(frac_lon=0.35, frac_lat=0.5):
    """A building covering roughly frac_lon x frac_lat of the cell."""
    x0, y0 = CELL[0]
    x1, y1 = CELL[2][0], CELL[2][1]
    return Polygon([(x0, y0), (x0 + (x1 - x0) * frac_lon, y0),
                    (x0 + (x1 - x0) * frac_lon, y0 + (y1 - y0) * frac_lat),
                    (x0, y0 + (y1 - y0) * frac_lat)])


def test_mapped_roofs_replace_the_landcover_mean():
    subs, diag = refine_imperviousness([_sub(70.0)], [_roof()], None, AOI)
    assert diag["applied"] and diag["n_refined"] == 1
    # roof ~17.5% of the cell + 10% allowance ~= 27-28%, far from the flat 70
    assert 20.0 < subs[0].pct_imperv < 35.0


def test_no_building_evidence_keeps_landcover_value():
    tiny = _roof(frac_lon=0.01, frac_lat=0.01)          # < 2% evidence threshold
    subs, diag = refine_imperviousness([_sub(70.0)], [tiny], None, AOI)
    assert subs[0].pct_imperv == 70.0
    assert diag["n_refined"] == 0 and diag["n_kept_landcover"] == 1


def test_no_buildings_is_a_documented_noop():
    subs, diag = refine_imperviousness([_sub(70.0)], [], None, AOI)
    assert subs[0].pct_imperv == 70.0 and diag["applied"] is False


def test_road_band_counts_toward_physical():
    g = nx.Graph()
    g.add_node("a", x=-123.5100, y=48.44045)            # street crossing the cell
    g.add_node("b", x=-123.5086, y=48.44045)
    g.add_edge("a", "b")
    no_road, _ = refine_imperviousness([_sub()], [_roof()], None, AOI)
    with_road, _ = refine_imperviousness([_sub()], [_roof()], g, AOI)
    assert with_road[0].pct_imperv > no_road[0].pct_imperv


def test_cap_holds_for_fully_built_cells():
    subs, _ = refine_imperviousness([_sub()], [_roof(1.0, 1.0)], None, AOI)
    assert subs[0].pct_imperv == 90.0
