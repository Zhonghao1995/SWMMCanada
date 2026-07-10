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
