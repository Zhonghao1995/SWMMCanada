"""Tests for catch-basin + parcel/building subcatchment delineation."""
from swmmcanada.build.models import JunctionIn, NetworkIn
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.sources.cities.victoria import delineate_catchbasin_subcatchments


def _pt(aid, x, y):
    return {"type": "Feature", "properties": {"AssetID": aid},
            "geometry": {"type": "Point", "coordinates": [x, y]}}


def _poly(ring):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.372, 48.418], [-123.368, 48.418], [-123.368, 48.422], [-123.372, 48.422], [-123.372, 48.418]]]})
NETWORK = NetworkIn(
    junctions=[JunctionIn("J1", invert_m=10, x=-123.371, y=48.419),
               JunctionIn("J2", invert_m=9, x=-123.369, y=48.421)],
    outfalls=[], conduits=[])
CATCHBASINS = [_pt("CB1", -123.3705, 48.4195), _pt("CB2", -123.3695, 48.4205), _pt("CB3", -123.370, 48.420)]
PARCELS = [_poly([[-123.3715, 48.4185], [-123.3695, 48.4185], [-123.3695, 48.4205],
                  [-123.3715, 48.4205], [-123.3715, 48.4185]])]
BUILDINGS = [_poly([[-123.3710, 48.4190], [-123.3705, 48.4190], [-123.3705, 48.4195],
                    [-123.3710, 48.4195], [-123.3710, 48.4190]])]


def test_delineation_routes_to_nearest_node_and_uses_parcel_imperv():
    subs, imperv_map, diag = delineate_catchbasin_subcatchments(NETWORK, CATCHBASINS, PARCELS, BUILDINGS, AOI)
    assert len(subs) >= 2
    for s in subs:
        assert s.outlet_node in {"J1", "J2"}          # routed to a real network node
        assert 1.0 <= s.pct_imperv <= 100.0
        assert s.area_ha > 0 and s.polygon is not None
    assert imperv_map                                  # at least one cell overlapped parcels
    assert diag["method"] == "catchbasin+parcel/building"
    assert diag["n_catchbasins"] == 3


def test_insufficient_catchbasins_returns_empty():
    subs, imperv_map, diag = delineate_catchbasin_subcatchments(NETWORK, [CATCHBASINS[0]], PARCELS, BUILDINGS, AOI)
    assert subs == [] and imperv_map == {}            # caller falls back to Voronoi
