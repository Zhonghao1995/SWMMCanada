"""What the frontage path must keep from the paths it replaced.

Each of these was built, measured and committed on a path that is no longer the default.
Work that only applies to a method nobody runs is work that was undone — and the way that
happens is silently, because everything still passes.
"""
import math

import pytest
from shapely.geometry import Polygon

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.subcatchments import SubcatchmentCell
from swmmcanada.network.synth import NetworkConfig, _build_subcatchments

AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.372, 48.424], [-123.364, 48.424], [-123.364, 48.429], [-123.372, 48.429],
    [-123.372, 48.424]]]})
NODES = {"MH1": (-123.3700, 48.4255), "MH2": (-123.3680, 48.4255)}


def _cell(ring, area_m2):
    poly = Polygon(ring)
    return SubcatchmentCell(polygon_4326=poly, area_m2=area_m2,
                            exterior=[(float(x), float(y)) for x, y in poly.exterior.coords])


#: A street-frontage strip: long along the road, shallow to the rear-lot line. This is the
#: shape the frontage split produces, and the shape `sqrt(area)` gets most wrong.
STRIP = _cell([(-123.3712, 48.42540), (-123.3688, 48.42540), (-123.3688, 48.42560),
               (-123.3712, 48.42560), (-123.3712, 48.42540)], 4000.0)
SQUARE = _cell([(-123.3690, 48.42540), (-123.3684, 48.42540), (-123.3684, 48.42585),
                (-123.3690, 48.42585), (-123.3690, 48.42540)], 4000.0)


class TestWidthIsStillAFlowLength:
    """`sqrt(area)` is the answer for a square cell and only for a square cell. Frontage
    cells are strips, which is exactly where it is wrong — and the frontage path had gone
    back to it."""

    def test_a_strip_is_wider_than_the_square_root_of_its_area(self):
        subs = _build_subcatchments(NODES, AOI, NetworkConfig(),
                                    cells={"MH1": STRIP, "MH2": SQUARE})
        strip = next(s for s in subs if s.outlet_node == "MH1")
        assert strip.width_m > 1.5 * math.sqrt(strip.area_ha * 1e4)

    def test_a_square_stays_about_where_it_was(self):
        subs = _build_subcatchments(NODES, AOI, NetworkConfig(),
                                    cells={"MH1": STRIP, "MH2": SQUARE})
        sq = next(s for s in subs if s.outlet_node == "MH2")
        assert 0.6 < sq.width_m / math.sqrt(sq.area_ha * 1e4) < 1.6

    def test_an_explicit_width_still_wins(self):
        """The DEM path measures flow length off the raster; that beats any geometric
        estimate and must not be overridden."""
        subs = _build_subcatchments(NODES, AOI, NetworkConfig(),
                                    cells={"MH1": STRIP}, widths={"MH1": 999.0})
        assert next(s for s in subs if s.outlet_node == "MH1").width_m == 999.0


class TestTheNoiseGateCoversEveryPath:
    """A delineation whose cells are mostly noise has not produced units, whichever method
    produced it. The check lived inside the terrain branch and the frontage path went
    straight past it."""

    def test_the_frontage_path_reports_whether_its_cells_are_units(self):
        """Behavioural, not a source search: the frontage branch returns early, so the gate
        being *defined* in the same function proves nothing about it being reached."""
        import networkx as nx

        from swmmcanada.network.delineate_dem import delineate_junction_subcatchments

        g = nx.Graph()
        for j, lon in enumerate((-123.3710, -123.3690, -123.3670)):
            g.add_node(f"s{j}", x=lon, y=48.4265)
        g.add_edge("s0", "s1")
        g.add_edge("s1", "s2")

        _subs, diag = delineate_junction_subcatchments(
            NODES, AOI, streets=g, service_mask=AOI.geometry)
        assert "noise_cell_share" in (diag.get("gate") or {}), (
            "the frontage path never asked whether it produced units")
