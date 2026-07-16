"""Cadastral cell snapping (ADR 0023 cut 2): lots join their fronting cell, coverage is
conserved, and no cadastre means a documented no-op."""
from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.parcels import snap_subcatchments_to_parcels

# two side-by-side ~55 m cells
L = [(-123.5100, 48.4400), (-123.5093, 48.4400), (-123.5093, 48.4409), (-123.5100, 48.4409)]
R = [(-123.5093, 48.4400), (-123.5086, 48.4400), (-123.5086, 48.4409), (-123.5093, 48.4409)]
AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.5102, 48.4398], [-123.5084, 48.4398], [-123.5084, 48.4411],
    [-123.5102, 48.4411], [-123.5102, 48.4398]]]})


def _subs():
    return [SubcatchmentIn(name="A", outlet_node="J1", area_ha=0.55, pct_imperv=40.0,
                           width_m=50.0, pct_slope=1.0, polygon=L + [L[0]]),
            SubcatchmentIn(name="B", outlet_node="J2", area_ha=0.55, pct_imperv=40.0,
                           width_m=50.0, pct_slope=1.0, polygon=R + [R[0]])]


def _parcel(x0, x1, y0=48.4402, y1=48.4407):
    return {"type": "Feature", "properties": {"PARCEL_CLASS": "Subdivision"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}}


def test_straddling_lot_joins_its_majority_cell():
    # lot mostly in A, poking ~30 m over the geometric midline into B
    lot = _parcel(-123.5098, -123.5089)
    subs, diag = snap_subcatchments_to_parcels(_subs(), [lot], AOI)
    assert diag["applied"] and diag["n_cells_reshaped"] == 2
    a = next(s for s in subs if s.name == "A")
    b = next(s for s in subs if s.name == "B")
    assert a.area_ha > b.area_ha + 0.2          # A absorbed the big overhang wholesale
    assert a.width_m > 50.0 > b.width_m         # widths follow areas (F-005)
    total = a.area_ha + b.area_ha
    ref, _ = snap_subcatchments_to_parcels(_subs(), [_parcel(-123.5099, -123.5095)], AOI)
    ref_total = sum(s.area_ha for s in ref)     # fully-inside lot: pure re-measure
    assert abs(total - ref_total) < ref_total * 0.02   # coverage conserved


def test_no_parcels_is_a_documented_noop():
    subs, diag = snap_subcatchments_to_parcels(_subs(), [], AOI)
    assert diag["applied"] is False and "geometric" in diag["source"]
    assert [s.area_ha for s in subs] == [0.55, 0.55]


def test_lot_lines_show_up_in_the_boundary():
    lot = _parcel(-123.5098, -123.5089)
    subs, _ = snap_subcatchments_to_parcels(_subs(), [lot], AOI)
    a = next(s for s in subs if s.name == "A")
    xs = {round(x, 6) for x, _ in a.polygon}
    assert round(-123.5089, 6) in xs        # the cadastral edge, not the Voronoi midline
