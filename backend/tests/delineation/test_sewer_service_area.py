"""Sewer service areas from published infrastructure (ADR 0029 Q1 / Q10, ADR 0031)."""
import pytest

from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     SewerServiceArea, SurfaceCatchment)
from swmmcanada.delineation.service_area import MAX_LATERAL_SNAP_M, derive_service_areas
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.loading import load_service_areas

AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.37, 48.42], [-123.36, 48.42], [-123.36, 48.43], [-123.37, 48.43],
    [-123.37, 48.42]]]})


def sanitary_network(n=3):
    js = [JunctionIn(f"SAN_M{i+1}", 10.0 - i, -123.368 + i * 0.002, 48.423 + i * 0.001,
                     system="sanitary") for i in range(n)]
    return NetworkIn(
        junctions=js,
        outfalls=[OutfallIn("SAN_WWTP", 6.0, -123.361, 48.428, system="sanitary")],
        conduits=[ConduitIn(f"SC{i}", js[i].name, js[i + 1].name, 200.0, system="sanitary")
                  for i in range(n - 1)]
                 + [ConduitIn("SCW", js[-1].name, "SAN_WWTP", 200.0, system="sanitary")])


def lateral(x0, y0, x1, y1):
    return {"geometry": {"type": "LineString", "coordinates": [[x0, y0], [x1, y1]]}}


class TestOutputType:
    def test_it_produces_service_areas_not_subcatchments(self):
        """The type is the guarantee: nothing here can reach [SUBCATCHMENTS]."""
        areas, _ = derive_service_areas(sanitary_network(), [], AOI, crs="EPSG:32610")
        assert areas and all(isinstance(a, SewerServiceArea) for a in areas)
        assert not any(isinstance(a, SurfaceCatchment) for a in areas)

    def test_areas_load_a_node_of_their_own_network(self):
        net = sanitary_network()
        areas, _ = derive_service_areas(net, [], AOI, crs="EPSG:32610")
        names = {n.name for n in list(net.junctions) + list(net.outfalls)}
        assert all(a.node in names for a in areas)

    def test_geometry_source_is_derived_and_loading_stays_unclaimed(self):
        areas, _ = derive_service_areas(sanitary_network(), [], AOI, crs="EPSG:32610")
        assert all(a.geometry_source == "derived" for a in areas)
        assert all(a.dwf_lps is None for a in areas), "loading is a separate step"

    def test_system_tag_can_be_combined(self):
        areas, _ = derive_service_areas(sanitary_network(), [], AOI, crs="EPSG:32610",
                                        system="combined")
        assert all(a.system == "combined" for a in areas)


class TestSeedEvidence:
    """A lateral says *this* property feeds *this* main. A manhole says only where the
    network is — a weaker claim, and the diagnostics must not blur them."""

    def test_laterals_are_preferred_when_they_snap(self):
        lats = [lateral(-123.3681, 48.4231, -123.3680, 48.4230),
                lateral(-123.3661, 48.4241, -123.3660, 48.4240),
                lateral(-123.3679, 48.4229, -123.3680, 48.4230)]
        _, diag = derive_service_areas(sanitary_network(), [], AOI, laterals=lats,
                                       crs="EPSG:32610")
        assert diag["seed_source"] == "lateral" and "lateral" in diag["evidence"]

    def test_no_laterals_falls_back_to_manholes(self):
        _, diag = derive_service_areas(sanitary_network(), [], AOI, crs="EPSG:32610")
        assert diag["seed_source"] == "manhole"
        assert "no laterals published" in diag["evidence"]

    def test_unusable_laterals_are_reported_differently_from_absent_ones(self):
        """Published-but-unsnappable usually means the lateral and main layers disagree
        about where the network is — a more interesting fact than 'none published'."""
        far = [lateral(-123.30, 48.40, -123.301, 48.401)] * 3
        _, diag = derive_service_areas(sanitary_network(), [], AOI, laterals=far,
                                       crs="EPSG:32610")
        assert diag["seed_source"] == "manhole_laterals_unusable"
        assert str(int(MAX_LATERAL_SNAP_M)) in diag["evidence"]

    def test_snap_limit_keeps_a_household_off_the_wrong_sewer(self):
        assert 0 < MAX_LATERAL_SNAP_M <= 100


class TestRefusals:
    def test_a_network_too_small_to_serve_anything_returns_nothing(self):
        net = NetworkIn(junctions=[JunctionIn("SAN_M1", 10.0, -123.368, 48.423,
                                              system="sanitary")],
                        outfalls=[], conduits=[])
        areas, diag = derive_service_areas(net, [], AOI, crs="EPSG:32610")
        assert areas == [] and "too small" in diag["reason"]


class TestSharedPipeline:
    """ADR 0029 Q10: the difference between a surface catchment and a service area is which
    network you hand it, not which code you run."""

    def test_it_reuses_the_storm_shaping_and_outlet_seams(self):
        import inspect

        from swmmcanada.delineation import service_area
        src = inspect.getsource(service_area)
        assert "base._shape_cells" in src and "base._outlet_resolver" in src

    def test_shape_method_is_reported_honestly(self):
        _, diag = derive_service_areas(sanitary_network(), [], AOI, crs="EPSG:32610")
        assert diag["shape_method"] == "voronoi"
        assert "voronoi" in diag["method"]


def test_derived_areas_flow_straight_into_the_loading_step():
    """End to end: the sanitary system stops being pipes with nothing in them."""
    areas, _ = derive_service_areas(sanitary_network(), [], AOI, crs="EPSG:32610")
    res = load_service_areas(areas)
    assert res.diagnostics["total_dwf_lps"] > 0
    assert all(a.dwf_lps and a.dwf_pattern for a in res.areas)
    # Every area rests on assumed density here, and the diagnostics say so rather than
    # letting a precise-looking flow imply precise evidence.
    assert res.diagnostics["pct_on_assumed_density"] == 100.0


class TestTheUnitIsTheNodeHereToo:
    """One service area per node, not one per lateral connection.

    A lateral says which node a property feeds — that is evidence, and it is why laterals
    beat manhole proximity. It is not a unit. Seeding a cell on every lateral endpoint gave
    a live downtown 1,392 areas over 195 sanitary nodes: 7.1 per node, a median of 190 m²,
    a third of them under 100 m², and 0.9 people each. Nothing is gained — the flow is
    applied at the node and sums to the same number either way — and a 190 m² polygon
    carrying nine tenths of a person is not something an engineer can check.

    This is the storm rule restated on the wastewater side: the unit is the model node,
    and the connection is the evidence that decides which node land belongs to.
    """

    def _laterals_into_one_node(self):
        """Four laterals reaching the same manhole from four sides."""
        x, y = -123.368, 48.423
        return [lateral(x + dx, y + dy, x, y) for dx, dy in
                ((-0.0004, 0.0), (0.0004, 0.0), (0.0, -0.0003), (0.0, 0.0003))]

    def test_four_laterals_on_one_node_make_one_area(self):
        net = sanitary_network()
        areas, _ = derive_service_areas(net, [], AOI, laterals=self._laterals_into_one_node(),
                                        crs="EPSG:32610")
        at_node = [a for a in areas if a.node == "SAN_M1"]
        assert len(at_node) == 1, f"{len(at_node)} areas on one node: {[a.name for a in at_node]}"

    def test_no_node_carries_more_than_one_area_per_contiguous_piece(self):
        net = sanitary_network()
        areas, diag = derive_service_areas(net, [], AOI, laterals=self._laterals_into_one_node(),
                                           crs="EPSG:32610")
        assert diag["n_service_areas"] <= len({a.node for a in areas}) * 2

    def test_the_land_is_not_lost_when_the_pieces_are_joined(self):
        net = sanitary_network()
        lats = self._laterals_into_one_node()
        merged, _ = derive_service_areas(net, [], AOI, laterals=lats, crs="EPSG:32610")
        assert sum(a.area_ha for a in merged) > 0
        # the whole AOI is still served: joining pieces must not drop any of them
        assert sum(a.area_ha for a in merged) == pytest.approx(
            AOI.area_km2 * 100.0, rel=0.02)

    def test_laterals_still_decide_which_node_land_belongs_to(self):
        """Merging must not cost the evidence — that is the whole reason laterals win."""
        net = sanitary_network()
        _areas, diag = derive_service_areas(net, [], AOI, laterals=self._laterals_into_one_node(),
                                            crs="EPSG:32610")
        assert diag["seed_source"] == "lateral"
