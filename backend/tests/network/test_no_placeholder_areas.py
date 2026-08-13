"""A node with no cell gets no subcatchment, not a made-up one.

`_build_subcatchments` fills a missing cell with a nominal 0.5 ha. That is a synthesis-era
convenience — there, every node sits on a street and always has one. On a published network
many nodes front no street at all, and the placeholder turned each into half a hectare of
invented land: Victoria's frontage split came out at 162% of its own AOI.

An area nobody measured must not be presented as one.
"""
import pytest

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.synth import NetworkConfig, _build_subcatchments
from swmmcanada.network.subcatchments import SubcatchmentCell
from shapely.geometry import Polygon

AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.372, 48.424], [-123.364, 48.424], [-123.364, 48.429], [-123.372, 48.429],
    [-123.372, 48.424]]]})

NODES = {"MH1": (-123.3700, 48.4255), "MH2": (-123.3680, 48.4255),
         "MH3": (-123.3660, 48.4255)}


def _cell(ring, area_m2):
    poly = Polygon(ring)
    return SubcatchmentCell(polygon_4326=poly, area_m2=area_m2,
                            exterior=[(float(x), float(y)) for x, y in poly.exterior.coords])


CELLS = {
    "MH1": _cell([(-123.3710, 48.4250), (-123.3690, 48.4250), (-123.3690, 48.4260),
                  (-123.3710, 48.4260), (-123.3710, 48.4250)], 4000.0),
    "MH2": _cell([(-123.3690, 48.4250), (-123.3670, 48.4250), (-123.3670, 48.4260),
                  (-123.3690, 48.4260), (-123.3690, 48.4250)], 6000.0),
}


class TestMeasuredCellsOnly:
    def test_a_node_without_a_cell_is_omitted(self):
        subs = _build_subcatchments(NODES, AOI, NetworkConfig(), cells=CELLS)
        assert {s.outlet_node for s in subs} == {"MH1", "MH2"}, (
            "MH3 has no measured frontage and must not appear")

    def test_the_measured_areas_are_the_measured_ones(self):
        subs = _build_subcatchments(NODES, AOI, NetworkConfig(), cells=CELLS)
        by_node = {s.outlet_node: s.area_ha for s in subs}
        assert by_node["MH1"] == pytest.approx(0.4)
        assert by_node["MH2"] == pytest.approx(0.6)

    def test_no_subcatchment_carries_the_nominal_placeholder(self):
        subs = _build_subcatchments(NODES, AOI, NetworkConfig(), cells=CELLS)
        nominal = NetworkConfig().sub_area_ha
        assert all(s.area_ha != nominal for s in subs)

    def test_the_total_cannot_exceed_what_was_measured(self):
        """The failure this replaces: 162% of the AOI, from invented land."""
        subs = _build_subcatchments(NODES, AOI, NetworkConfig(), cells=CELLS)
        assert sum(s.area_ha for s in subs) == pytest.approx(1.0)


class TestSynthesisIsUnaffected:
    def test_without_cells_the_nominal_area_still_applies(self):
        """Synthesis lays its nodes along the streets and delineates them itself; the
        no-cells path is its own and must keep working."""
        subs = _build_subcatchments(NODES, AOI, NetworkConfig(), cells=None)
        assert len(subs) == len(NODES)


class TestTheFallbackInventsToo:
    """The same rule when the tiling is computed inside `_build_subcatchments`.

    The guard above only fires when a caller hands cells in. The nearest-node fallback lets
    the builder tile for itself, and there a node the tiling misses still collected the
    nominal half hectare: measured on a live downtown, 11 cells carrying 5.5 ha between them
    and no polygon at all. They generate runoff in SWMM and are invisible to every geometric
    check — overlap, containment, coverage — because there is nothing to check.
    """

    def test_a_node_the_tiling_misses_gets_no_subcatchment(self):
        nodes = dict(NODES)
        nodes["MH_FAR"] = (-123.300, 48.500)          # far outside the AOI
        subs = _build_subcatchments(nodes, AOI, NetworkConfig())
        assert "S_MH_FAR" not in {s.name for s in subs}

    def test_no_cell_carries_area_without_a_polygon(self):
        nodes = dict(NODES)
        nodes["MH_FAR"] = (-123.300, 48.500)
        subs = _build_subcatchments(nodes, AOI, NetworkConfig())
        ghosts = [s.name for s in subs if (s.area_ha or 0) > 0 and not s.polygon]
        assert ghosts == [], f"area with no geometry: {ghosts}"

    def test_a_network_with_no_area_to_tile_still_gets_its_nominal_cells(self):
        """Synthesis has no AOI polygon to delineate against; the nominal path is all it
        has, and removing it would leave a synthesised network with no runoff at all."""
        subs = _build_subcatchments(NODES, None, NetworkConfig())
        assert len(subs) == len(NODES)
        assert all((s.area_ha or 0) > 0 for s in subs)
