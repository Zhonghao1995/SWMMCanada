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
    assert diag["method"].startswith("catchbasin+parcel/building")  # 1 parcel -> Voronoi-shaped
    assert diag["n_catchbasins"] == 3


def test_insufficient_catchbasins_returns_empty():
    subs, imperv_map, diag = delineate_catchbasin_subcatchments(NETWORK, [CATCHBASINS[0]], PARCELS, BUILDINGS, AOI)
    assert subs == [] and imperv_map == {}            # caller falls back to Voronoi


# A grid of parcels tiling the AOI -> cells are shaped by lot lines, not a Voronoi bisector.
_GRID_PARCELS = [
    _poly([[-123.372, 48.418], [-123.370, 48.418], [-123.370, 48.420], [-123.372, 48.420], [-123.372, 48.418]]),
    _poly([[-123.370, 48.418], [-123.368, 48.418], [-123.368, 48.420], [-123.370, 48.420], [-123.370, 48.418]]),
    _poly([[-123.372, 48.420], [-123.370, 48.420], [-123.370, 48.422], [-123.372, 48.422], [-123.372, 48.420]]),
    _poly([[-123.370, 48.420], [-123.368, 48.420], [-123.368, 48.422], [-123.370, 48.422], [-123.370, 48.420]]),
]


def test_parcel_shaped_cells_when_parcels_available():
    subs, imperv_map, diag = delineate_catchbasin_subcatchments(
        NETWORK, CATCHBASINS, _GRID_PARCELS, BUILDINGS, AOI)
    assert diag["method"] == "catchbasin+parcel/building (parcel-shaped)"  # shape from real parcels
    assert len(subs) >= 2
    for s in subs:
        assert s.outlet_node in {"J1", "J2"}
        assert s.area_ha > 0 and s.polygon is not None


# --- coverage fix (PRD #3): a catch basin assigned diagonal (disconnected) parcels --------
# 4 quadrant parcels tile the AOI; the 3 catch basins are placed so the centre one wins both
# diagonal corners (bl + tr), which share only a point -> its union is two pieces. The old
# "keep largest polygon" rule dropped one piece, leaving a blank hole. The fix keeps both.
_QUAD = [
    _poly([[-123.372, 48.418], [-123.370, 48.418], [-123.370, 48.420], [-123.372, 48.420], [-123.372, 48.418]]),
    _poly([[-123.370, 48.418], [-123.368, 48.418], [-123.368, 48.420], [-123.370, 48.420], [-123.370, 48.418]]),
    _poly([[-123.372, 48.420], [-123.370, 48.420], [-123.370, 48.422], [-123.372, 48.422], [-123.372, 48.420]]),
    _poly([[-123.370, 48.420], [-123.368, 48.420], [-123.368, 48.422], [-123.370, 48.422], [-123.370, 48.420]]),
]
_CBS_DIAG = [_pt("A", -123.370, 48.420), _pt("B", -123.3688, 48.4188), _pt("C", -123.3712, 48.4212)]


def _coverage(subs):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    union = unary_union([Polygon(s.polygon) for s in subs if s.polygon])
    return union.intersection(AOI.geometry).area / AOI.geometry.area


def test_disconnected_catchbasin_pieces_are_kept_no_blank():
    from swmmcanada.validate import MethodDescriptor, validate_model

    subs, _, _ = delineate_catchbasin_subcatchments(NETWORK, _CBS_DIAG, _QUAD, [], AOI)
    assert _coverage(subs) > 0.98                       # no blank holes (the Victoria symptom)
    assert len(subs) >= 4                               # the diagonal basin split into 2 pieces
    for s in subs:
        assert s.outlet_node in {"J1", "J2"} and s.area_ha > 0
    # ...and the validation layer's blank-holes check now passes on this model (the acceptance proof)
    report = validate_model(NETWORK, subs, AOI,
                            method=MethodDescriptor("catchbasin_parcel", "nearest inlet service area", "medium"))
    assert next(c for c in report.checks if c.id == "aoi_coverage").passed


def test_real_network_delineation_is_deterministic():
    """Same inputs delineated twice -> identical subcatchments (reproducible builds; PRD #2)."""
    a, _, _ = delineate_catchbasin_subcatchments(NETWORK, CATCHBASINS, _GRID_PARCELS, BUILDINGS, AOI)
    b, _, _ = delineate_catchbasin_subcatchments(NETWORK, CATCHBASINS, _GRID_PARCELS, BUILDINGS, AOI)
    assert [s.name for s in a] == [s.name for s in b]
    for sa, sb in zip(a, b):
        assert sa.outlet_node == sb.outlet_node
        assert sa.area_ha == sb.area_ha
        assert sa.polygon == sb.polygon


# --- remainder-donut "parcels" (Moncton downtown finding) --------------------------------

def _donut(ring, holes):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [ring] + holes}}


def test_remainder_donut_parcel_is_dropped_and_streets_revert_to_voronoi():
    """A street right-of-way polygon shipped AS a parcel (holes = the real lots) must
    not be assigned whole to one catch basin: it is dropped, its road area returns to
    the street-sliver path, and no cell blankets the lots (the 22% Moncton overlap)."""
    lots = _GRID_PARCELS
    # donut = whole AOI ring with the four lots as holes -> hole share ~= 75% of exterior
    donut = _donut(
        [[-123.372, 48.418], [-123.368, 48.418], [-123.368, 48.422], [-123.372, 48.422],
         [-123.372, 48.418]],
        [p["geometry"]["coordinates"][0] for p in lots])
    subs, _, diag = delineate_catchbasin_subcatchments(
        NETWORK, CATCHBASINS, lots + [donut], BUILDINGS, AOI)
    assert diag["n_parcels_dropped_remainder"] == 1
    from shapely.geometry import Polygon
    geoms = [Polygon(s.polygon, s.holes or None) for s in subs]
    total = sum(g.area for g in geoms)
    overlap = 0.0
    for i, g in enumerate(geoms):
        for h in geoms[i + 1:]:
            overlap += g.intersection(h).area
    assert overlap / total < 0.02                      # near-disjoint, not 22%


def test_wraparound_cell_keeps_its_holes(monkeypatch):
    """A cell whose polygon carries interior rings (it wraps around land that drains
    elsewhere) must emit them on SurfaceCatchment.holes — analysis geometry, round-2
    F-005. Before the fix this path stored the exterior only, so a wrap-around cell
    blanketed everything inside it (the giant Moncton piece had 0 of its 69 holes)."""
    from shapely.geometry import Polygon

    from swmmcanada.sources.cities import base as b

    donut_ll = Polygon(
        [(-123.3718, 48.4186), (-123.3686, 48.4186), (-123.3686, 48.4214),
         (-123.3718, 48.4214)],
        [[(-123.3710, 48.4194), (-123.3694, 48.4194), (-123.3694, 48.4206),
          (-123.3710, 48.4206)]])
    monkeypatch.setattr(
        b, "_parcel_cells",
        lambda *a, **k: {"CB1": [b._Cell(area_m2=1e4, polygon_4326=donut_ll)]})
    subs, _, diag = b.delineate_catchbasin_subcatchments(
        NETWORK, CATCHBASINS, PARCELS, BUILDINGS, AOI)
    cell = next(s for s in subs if s.name.startswith("S_CB1"))
    assert cell.holes and len(cell.holes) == 1, "interior ring vanished from the cell"
