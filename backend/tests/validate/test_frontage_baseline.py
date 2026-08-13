"""Locked baseline for the default storm delineation.

The existing baselines pin methods that are no longer the default — they still pass and no
longer describe what a build produces. This pins the frontage split on the checked-in
Victoria fixture, so a change to it shows up as a reviewable diff instead of a silent shift
in every model the tool makes.

Numbers are fixture-scale. The live-AOI figures live in ASSUMPTIONS.md.
"""
import json
from pathlib import Path

import networkx as nx
import pytest

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.delineate_dem import delineate_junction_subcatchments
from swmmcanada.network.service_area import MIN_CELL_HA
from swmmcanada.sources.cities.victoria import build_victoria_network
from swmmcanada.validate import schema

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "victoria"

# --- the locked baseline (today's behaviour; a legitimate change is a one-line diff) ---
VIC_FRONTAGE_CELLS = 61
VIC_FRONTAGE_MEDIAN_HA = 1.83
VIC_FRONTAGE_TOLERANCE = 0.30     # fixture-scale medians are coarse; a shift beyond this
                                  # is a change in behaviour, not in floating point

# The fixture's cells run larger than a live AOI's (0.36 ha median) because its street grid
# is synthetic and coarse — five lines each way over the whole extent, where a real block
# has one every 80 m or so. The baseline pins behaviour, not realism; ASSUMPTIONS.md carries
# the live figures.


def _load(name):
    d = json.loads((FIXTURES / f"{name}.geojson").read_text())
    return d["features"] if isinstance(d, dict) else d


@pytest.fixture(scope="module")
def network():
    return build_victoria_network(
        mains=_load("mains"), manholes=_load("manholes"),
        fittings=_load("fittings"), outfalls=_load("outfalls")).network


@pytest.fixture(scope="module")
def aoi(network):
    xs = [j.x for j in network.junctions]
    ys = [j.y for j in network.junctions]
    pad = 0.0006
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [min(xs) - pad, min(ys) - pad], [max(xs) + pad, min(ys) - pad],
        [max(xs) + pad, max(ys) + pad], [min(xs) - pad, max(ys) + pad],
        [min(xs) - pad, min(ys) - pad]]]})


@pytest.fixture(scope="module")
def streets(network):
    """A grid over the fixture's own extent. OSM is not reachable from a test, and the
    baseline is about the SPLIT rather than about any particular street layout."""
    xs = [j.x for j in network.junctions]
    ys = [j.y for j in network.junctions]
    g = nx.Graph()
    lons = [min(xs) + (max(xs) - min(xs)) * i / 4 for i in range(5)]
    lats = [min(ys) + (max(ys) - min(ys)) * i / 4 for i in range(5)]
    for r, lat in enumerate(lats):
        for c, lon in enumerate(lons):
            g.add_node(f"n{r}{c}", x=lon, y=lat)
    for r in range(5):
        for c in range(4):
            g.add_edge(f"n{r}{c}", f"n{r}{c+1}")
            g.add_edge(f"n{c}{r}", f"n{c+1}{r}")
    return g


@pytest.fixture(scope="module")
def result(network, aoi, streets):
    return delineate_junction_subcatchments(
        {j.name: (j.x, j.y) for j in network.junctions}, aoi,
        streets=streets, service_mask=aoi.geometry, min_cell_ha=MIN_CELL_HA)


class TestTheLockedShape:
    def test_cell_count(self, result):
        subs, _ = result
        assert len(subs) == VIC_FRONTAGE_CELLS

    def test_median_cell_size(self, result):
        subs, _ = result
        areas = sorted(s.area_ha for s in subs)
        median = areas[len(areas) // 2]
        assert median == pytest.approx(VIC_FRONTAGE_MEDIAN_HA, abs=VIC_FRONTAGE_TOLERANCE)

    def test_it_took_the_frontage_split(self, result):
        _subs, diag = result
        assert diag["gate"]["decision"] == "corridor_frontage"
        assert "street segment" in diag["split"]


class TestTheQualitiesThatMatter:
    """These are the reason the method was changed. A future edit may move the counts; it
    must not move these."""

    def test_no_cell_is_at_noise_scale(self, result):
        subs, _ = result
        assert all(s.area_ha >= MIN_CELL_HA for s in subs)

    def test_the_sizes_are_even(self, result):
        """Even distribution is the property the inlet and terrain methods lacked."""
        subs, _ = result
        areas = sorted(s.area_ha for s in subs)
        mean = sum(areas) / len(areas)
        assert mean / areas[len(areas) // 2] < 2.0

    def test_every_cell_drains_to_a_real_node(self, network, result):
        subs, _ = result
        names = {n.name for n in list(network.junctions) + list(network.outfalls)}
        assert all(s.outlet_node in names for s in subs)

    def test_width_is_not_the_square_root_of_area(self, result):
        subs, _ = result
        assert all(s.width_m != pytest.approx((s.area_ha * 1e4) ** 0.5) for s in subs)

    def test_no_cell_carries_a_fabricated_area(self, result):
        from swmmcanada.network.synth import NetworkConfig

        subs, _ = result
        assert all(s.area_ha != NetworkConfig().sub_area_ha for s in subs)
