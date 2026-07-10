"""Build a GeoJSON FeatureCollection of the synthesised model (subcatchments, conduits,
junctions, outfall) for the frontend map preview. One FeatureCollection; each feature
carries a `kind` so the map can split it into toggleable layers. Coords are lon/lat (WGS84).
"""
from typing import List

from swmmcanada.build.models import NetworkIn, SubcatchmentIn


def network_geojson(network: NetworkIn, subcatchments: List[SubcatchmentIn]) -> dict:
    coord = {}
    for n in list(network.junctions) + list(network.outfalls):
        coord[n.name] = [float(n.x), float(n.y)]

    features = []

    for s in subcatchments:
        if not s.polygon:
            continue
        ring = [[float(x), float(y)] for x, y in s.polygon]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {
                # First-pass QC field set (ADR 0019): what an engineer sanity-checks on a
                # click. The full parameter table stays in the downloadable package.
                "kind": "subcatchment", "id": s.name, "area_ha": round(s.area_ha, 4),
                "pct_imperv": round(s.pct_imperv, 1), "cn": round(s.cn, 1),
                "pct_slope": round(s.pct_slope, 2),
                "outlet_node": s.outlet_node, "width_m": round(s.width_m, 1),
            },
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })

    for c in network.conduits:
        if c.from_node in coord and c.to_node in coord:
            features.append({
                "type": "Feature",
                "properties": {"kind": "conduit", "id": c.name, "diameter_m": c.diameter_m,
                               "length_m": round(c.length_m, 1), "roughness_n": c.roughness_n,
                               "from_node": c.from_node, "to_node": c.to_node,
                               "system": c.system},
                "geometry": {"type": "LineString", "coordinates": [coord[c.from_node], coord[c.to_node]]},
            })

    for j in network.junctions:
        features.append({
            "type": "Feature",
            "properties": {"kind": "junction", "id": j.name, "invert_m": round(j.invert_m, 2),
                           "max_depth_m": round(j.max_depth_m, 2), "system": j.system},
            "geometry": {"type": "Point", "coordinates": [float(j.x), float(j.y)]},
        })

    for o in network.outfalls:
        features.append({
            "type": "Feature",
            # "outfall_type", not "kind": the layer-splitting key is already "kind".
            "properties": {"kind": "outfall", "id": o.name, "invert_m": round(o.invert_m, 2),
                           "outfall_type": o.kind, "system": o.system},
            "geometry": {"type": "Point", "coordinates": [float(o.x), float(o.y)]},
        })

    return {"type": "FeatureCollection", "features": features}
