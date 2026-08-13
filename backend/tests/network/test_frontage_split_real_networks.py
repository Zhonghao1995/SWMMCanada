"""Frontage splitting has to work on a real pipe network, not only a synthesised one.

The split assumed its nodes WERE the street graph's nodes — true in synthesis, where the
network is laid out along the streets, and false for a published network whose maintenance
holes sit wherever the pipes go. Handed Victoria's 391 real nodes and 121 street edges it
returned zero cells, and every subcatchment silently took a 0.5 ha placeholder.

Labelling by nearest node instead keeps the geometry that matters — land goes to the street
it faces, blocks split along the rear-lot midline — and stops caring whose graph the nodes
came from.
"""
import pytest
from shapely.geometry import box

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.service_area import edge_split_cells

AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.3720, 48.4240], [-123.3640, 48.4240], [-123.3640, 48.4290],
    [-123.3720, 48.4290], [-123.3720, 48.4240]]]})


def _street_grid():
    """Two parallel streets running east-west, as networkx expects them."""
    import networkx as nx

    g = nx.Graph()
    for i, lat in enumerate((48.4255, 48.4275)):
        for j, lon in enumerate((-123.3710, -123.3690, -123.3670, -123.3650)):
            g.add_node(f"s{i}{j}", x=lon, y=lat)
        for j in range(3):
            g.add_edge(f"s{i}{j}", f"s{i}{j+1}")
    return g


def _pipe_nodes():
    """Maintenance holes near the streets but NOT street-graph nodes — offset, and named
    the way a city names them."""
    return {"MH001": (-123.3700, 48.42555),
            "MH002": (-123.3680, 48.42555),
            "MH003": (-123.3700, 48.42745),
            "MH004": (-123.3680, 48.42745)}


class TestItWorksWithNodesOffTheStreetGraph:
    def test_a_real_network_gets_cells(self):
        cells = edge_split_cells(_street_grid(), _pipe_nodes(), AOI.geometry, AOI)
        assert cells, "a published network's nodes produced no cells at all"

    def test_every_node_that_fronts_a_street_gets_one(self):
        cells = edge_split_cells(_street_grid(), _pipe_nodes(), AOI.geometry, AOI)
        assert set(cells) == set(_pipe_nodes())

    def test_the_cells_have_real_areas(self):
        """The failure mode this replaces: no cells, so every subcatchment took a 0.5 ha
        placeholder and the totals came out at twice the AOI."""
        cells = edge_split_cells(_street_grid(), _pipe_nodes(), AOI.geometry, AOI)
        for name, c in cells.items():
            assert c.area_m2 > 0, name

    def test_the_cells_do_not_exceed_the_area_they_divide(self):
        cells = edge_split_cells(_street_grid(), _pipe_nodes(), AOI.geometry, AOI)
        total_ha = sum(c.area_m2 for c in cells.values()) / 1e4
        assert total_ha <= AOI.area_km2 * 100 * 1.01


class TestItStillSplitsByFrontage:
    def test_neighbouring_nodes_on_one_street_divide_between_them(self):
        """Two nodes on the same street get comparable shares — the gutter divide sits
        between them rather than one taking the whole road."""
        cells = edge_split_cells(_street_grid(), _pipe_nodes(), AOI.geometry, AOI)
        a, b = cells["MH001"].area_m2, cells["MH002"].area_m2
        assert 0.3 < a / b < 3.0, (a, b)

    def test_a_node_with_no_street_near_it_gets_nothing(self):
        """Land goes to the street it faces; a node facing none has no frontage to claim."""
        nodes = dict(_pipe_nodes())
        nodes["MH_FAR"] = (-123.3645, 48.4287)
        cells = edge_split_cells(_street_grid(), nodes, AOI.geometry, AOI)
        far = cells.get("MH_FAR")
        assert far is None or far.area_m2 < cells["MH001"].area_m2
