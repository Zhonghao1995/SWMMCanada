"""TDD for preview.network_geojson — the map-preview FeatureCollection."""
import networkx as nx

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network import synthesise_network
from swmmcanada.preview import network_geojson

BOX = {
    "type": "Polygon",
    "coordinates": [[[-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]],
}


def _graph():
    g = nx.Graph()
    pts = {"O": (-75.695, 45.412, 90.0), "A": (-75.685, 45.412, 95.0),
           "B": (-75.685, 45.418, 100.0), "C": (-75.695, 45.418, 98.0)}
    for n, (x, y, e) in pts.items():
        g.add_node(n, x=x, y=y, elev=e)
    g.add_edge("O", "A"); g.add_edge("A", "B"); g.add_edge("B", "C"); g.add_edge("O", "C")
    return g


def _model():
    sn = synthesise_network(_graph(), aoi=aoi_from_geojson(BOX))
    return sn.network, sn.subcatchments


def test_network_geojson_layers():
    sn = synthesise_network(_graph(), aoi=aoi_from_geojson(BOX))
    fc = network_geojson(sn.network, sn.subcatchments)

    assert fc["type"] == "FeatureCollection"
    kinds = {f["properties"]["kind"] for f in fc["features"]}
    assert {"subcatchment", "conduit", "junction", "outfall"}.issubset(kinds)

    by_kind = {}
    for f in fc["features"]:
        by_kind.setdefault(f["properties"]["kind"], []).append(f)
    # geometry sanity
    assert all(f["geometry"]["type"] == "Polygon" for f in by_kind["subcatchment"])
    assert all(f["geometry"]["type"] == "LineString" for f in by_kind["conduit"])
    assert len(by_kind["outfall"]) == 1
    # subcatchments carry the derived/placeholder params for popups
    sub = by_kind["subcatchment"][0]["properties"]
    assert {"area_ha", "pct_imperv", "cn", "pct_slope"}.issubset(sub)
    # closed rings
    for f in by_kind["subcatchment"]:
        coords = f["geometry"]["coordinates"][0]
        assert coords[0] == coords[-1]


def test_features_carry_the_first_pass_qc_fields():
    """ADR 0019: the preview IS the click-inspect data contract — every element carries
    the fields an engineer sanity-checks on click."""
    sn = synthesise_network(_graph(), aoi=aoi_from_geojson(BOX))
    fc = network_geojson(sn.network, sn.subcatchments)
    by_kind = {}
    for f in fc["features"]:
        by_kind.setdefault(f["properties"]["kind"], []).append(f["properties"])

    assert {"outlet_node", "width_m"}.issubset(by_kind["subcatchment"][0])
    assert {"length_m", "roughness_n", "from_node", "to_node", "system"}.issubset(by_kind["conduit"][0])
    assert {"invert_m", "max_depth_m", "system"}.issubset(by_kind["junction"][0])
    out = by_kind["outfall"][0]
    assert {"invert_m", "outfall_type", "system"}.issubset(out)
    assert out["kind"] == "outfall"          # the layer key survives the type field rename


class TestSanitaryServiceAreasAreOnTheMap:
    """Wastewater land is drawn too, and is not a subcatchment.

    A sanitary service area is divided on a different basis from a storm cell — sewage
    reaches a pipe through a lateral, not by running over the ground, so the boundary
    follows parcels and connections and can cross a topographic divide. The model carries
    both, and the preview carried only one, so wastewater land was invisible to anyone
    looking at the map: no way to check it, and no way to see that it differs.
    """

    def _area(self, **kw):
        from swmmcanada.build.models import SewerServiceArea

        ring = [(-75.695, 45.412), (-75.690, 45.412), (-75.690, 45.416), (-75.695, 45.416)]
        return SewerServiceArea(name=kw.pop("name", "SAN_A1"), node=kw.pop("node", "SAN_J1"),
                                area_ha=kw.pop("area_ha", 2.5), polygon=ring, **kw)

    def _features(self, service_areas):
        net, subs = _model()
        gj = network_geojson(net, subs, service_areas=service_areas)
        return [f for f in gj["features"] if f["properties"]["kind"] == "service_area"]

    def test_a_service_area_becomes_a_feature(self):
        assert len(self._features([self._area()])) == 1

    def test_it_is_not_labelled_a_subcatchment(self):
        """The distinction is the point: one produces runoff, the other loads a node."""
        net, subs = _model()
        gj = network_geojson(net, subs, service_areas=[self._area()])
        kinds = {f["properties"]["kind"] for f in gj["features"]}
        assert "service_area" in kinds
        sub_ids = [f["properties"]["id"] for f in gj["features"]
                   if f["properties"]["kind"] == "subcatchment"]
        assert "SAN_A1" not in sub_ids

    def test_it_carries_what_an_engineer_checks_on_click(self):
        p = self._features([self._area(dwf_lps=1.75, population=180.0, system="sanitary")])[0]
        assert p["properties"]["node"] == "SAN_J1"
        assert p["properties"]["system"] == "sanitary"
        assert p["properties"]["dwf_lps"] == 1.75
        assert p["properties"]["area_ha"] == 2.5

    def test_a_hole_survives(self):
        """Water-aware synthesis punches holes; a filled-in hole would overstate the load."""
        a = self._area(holes=[[(-75.694, 45.413), (-75.693, 45.413),
                               (-75.693, 45.414), (-75.694, 45.414)]])
        geom = self._features([a])[0]["geometry"]
        assert len(geom["coordinates"]) == 2

    def test_none_is_the_old_behaviour(self):
        assert self._features(None) == []
