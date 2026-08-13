"""Kerb-conditioned delineation (规划书 §4, priority 2).

The conditioning primitives are tested on arrays elsewhere. This checks the thing that
matters: that a kerb published by a city actually changes where a subcatchment boundary
falls, and that a city publishing none is left exactly where it was.
"""
import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.crs import CRS

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.delineate_dem import (
    DemDelineationConfig,
    delineate_junction_subcatchments,
)
from swmmcanada.validate import schema

DEM_CRS = "EPSG:32618"
RES = 10.0
N = 100
X0, Y0 = 500_000.0, 5_000_000.0
_TO_LL = Transformer.from_crs(DEM_CRS, "EPSG:4326", always_xy=True).transform


def _write_dem(tmp_path, array, name="dem.tif"):
    path = tmp_path / name
    with rasterio.open(path, "w", driver="GTiff", height=array.shape[0],
                       width=array.shape[1], count=1, dtype="float32",
                       crs=CRS.from_string(DEM_CRS),
                       transform=Affine(RES, 0, X0, 0, -RES, Y0), nodata=-9999.0) as dst:
        dst.write(array.astype("float32"), 1)
    return path


GUTTER_ROW = 60


def _street_dem(cross_slope=0.10, along_grade=0.001):
    """A hillside falling south onto a street, with a gutter running east along row 60.

    This is the geometry a kerb is actually built in. A kerb across a valley is simply
    overtopped once depressions are filled — the water behind it has nowhere else to go, and
    that is correct. A kerb ALONGSIDE the gutter is different: the water behind it does have
    somewhere else to go, along the gutter to an opening, which is the routing a bare DEM
    gets wrong.
    """
    rows = np.arange(N)[:, None]
    hill = np.maximum(GUTTER_ROW - rows, 0) * cross_slope * RES     # falls onto the street
    back = np.maximum(rows - GUTTER_ROW, 0) * cross_slope * RES     # far side rises again
    along = (N - np.arange(N))[None, :] * along_grade * RES         # gutter grade, eastward
    return hill + back + along + 100.0


def _utm(col, row):
    return X0 + col * RES, Y0 - row * RES


def _aoi(margin=5):
    x1, y1 = _utm(margin, N - margin)
    x2, y2 = _utm(N - margin, margin)
    lo1, la1 = _TO_LL(x1, y1)
    lo2, la2 = _TO_LL(x2, y2)
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [lo1, la1], [lo2, la1], [lo2, la2], [lo1, la2], [lo1, la1]]]})


def _inlets():
    """Two inlets in the gutter."""
    return {name: _TO_LL(*_utm(col, GUTTER_ROW))
            for name, col in (("CB_W", 30), ("CB_E", 70))}


AOI = _aoi()
CFG = DemDelineationConfig(slope_gate_pct=3.0)


def _kerb_along_the_street():
    """The kerb face, one row uphill of the gutter, spanning the block."""
    from shapely.geometry import LineString

    _x0, y = _utm(0, GUTTER_ROW - 2)
    x_w, _ = _utm(2, 0)
    x_e, _ = _utm(N - 2, 0)
    return [LineString([(x_w, y), (x_e, y)])]


def _gate_at(col):
    """A kerb drop: the one place hillside runoff crosses into the gutter."""
    from shapely.geometry import Point

    return [Point(*_utm(col, GUTTER_ROW - 2))]


class TestItChangesTheAnswer:
    def test_a_kerb_changes_where_water_goes(self):
        """The mechanism, checked directly on the conditioned surface.

        Going through the full delineator hides this: its uncovered-cell absorption assigns
        by proximity and swamps the routing difference on a synthetic block. So the claim is
        tested where it is made — D8 on the surface we produced — using pyflwdir rather than
        any internal of ours.
        """
        import pyflwdir
        from shapely.geometry import LineString, Point

        from swmmcanada.network.urban_conditioning import condition_urban_dem

        transform = Affine(RES, 0, X0, 0, -RES, Y0)
        dem = _street_dem()

        x_w, y = _utm(2, GUTTER_ROW - 2)
        x_e, _ = _utm(N - 2, 0)
        kerb = [LineString([(x_w, y), (x_e, y)])]

        plain_dir = pyflwdir.from_dem(dem, transform=transform, latlon=False).idxs_ds
        kerbed, _ = condition_urban_dem(dem, transform, kerbs=kerb)
        kerbed_dir = pyflwdir.from_dem(kerbed, transform=transform,
                                       latlon=False).idxs_ds

        changed = int((plain_dir != kerbed_dir).sum())
        assert changed > 0, "the kerb changed nobody's flow direction"

    def test_a_gate_restores_the_crossing_it_blocks(self):
        """A kerb with an opening must route differently from a kerb without one, or the
        opening is decorative."""
        import pyflwdir
        from shapely.geometry import LineString, Point

        from swmmcanada.network.urban_conditioning import condition_urban_dem

        transform = Affine(RES, 0, X0, 0, -RES, Y0)
        dem = _street_dem()
        x_w, y = _utm(2, GUTTER_ROW - 2)
        x_e, _ = _utm(N - 2, 0)
        kerb = [LineString([(x_w, y), (x_e, y)])]
        gate = [Point(*_utm(30, GUTTER_ROW - 2))]

        shut, _ = condition_urban_dem(dem, transform, kerbs=kerb)
        open_, diag = condition_urban_dem(dem, transform, kerbs=kerb, openings=gate)
        assert diag["n_opening_cells"] > 0

        a = pyflwdir.from_dem(shut, transform=transform, latlon=False).idxs_ds
        b = pyflwdir.from_dem(open_, transform=transform, latlon=False).idxs_ds
        assert int((a != b).sum()) > 0, "the opening changed nothing"

    def test_a_steep_grade_overwhelms_a_kerb(self, tmp_path):
        """Physical, not incidental: a 150 mm kerb does not hold water running down a hill,
        and conditioning must not pretend otherwise."""
        dem = _write_dem(tmp_path, _street_dem(along_grade=0.05))
        plain, _ = delineate_junction_subcatchments(_inlets(), AOI, dem_path=dem, config=CFG)
        kerbed, _ = delineate_junction_subcatchments(
            _inlets(), AOI, dem_path=dem, config=CFG, kerbs=_kerb_along_the_street())
        assert [(s.name, round(s.area_ha, 3)) for s in plain] == \
               [(s.name, round(s.area_ha, 3)) for s in kerbed]

    def test_the_conditioning_is_reported(self, tmp_path):
        dem = _write_dem(tmp_path, _street_dem())
        _s, diag = delineate_junction_subcatchments(
            _inlets(), AOI, dem_path=dem, config=CFG, kerbs=_kerb_along_the_street())
        assert diag["urban_conditioning"]["n_kerb_cells"] > 0


class TestItDoesNotDisturbCitiesWithoutKerbs:
    def test_no_kerbs_reproduces_the_previous_delineation(self, tmp_path):
        """Five of the fleet publish kerbs. The other thirty must be untouched."""
        dem = _write_dem(tmp_path, _street_dem())
        before, d0 = delineate_junction_subcatchments(_inlets(), AOI, dem_path=dem, config=CFG)
        after, d1 = delineate_junction_subcatchments(_inlets(), AOI, dem_path=dem, config=CFG,
                                                     kerbs=[], openings=[], buildings=[])
        assert [(s.name, round(s.area_ha, 6)) for s in before] == \
               [(s.name, round(s.area_ha, 6)) for s in after]
        assert d1["urban_conditioning"]["applied"] is False

    def test_the_method_label_still_says_what_produced_it(self, tmp_path):
        dem = _write_dem(tmp_path, _street_dem())
        _s, diag = delineate_junction_subcatchments(
            _inlets(), AOI, dem_path=dem, config=CFG, kerbs=_kerb_along_the_street())
        assert diag["method"] in (schema.METHOD_JUNCTION_DEM,
                                  schema.METHOD_JUNCTION_VORONOI)


class TestPourPointSnapping:
    """规划书 §4 priority 3: a published inlet marks the structure, not the pixel water
    arrives at. Off by a metre and D8 hands it a basin of one pixel."""

    def test_snapping_is_reported_when_asked_for(self, tmp_path):
        dem = _write_dem(tmp_path, _street_dem())
        _s, diag = delineate_junction_subcatchments(
            _inlets(), AOI, dem_path=dem, config=CFG, snap_pour_points=True)
        assert diag["pour_point_snapping"]["search_radius_m"] > 0

    def test_it_is_off_unless_asked(self, tmp_path):
        """Existing cities keep their delineation until the resolver opts them in."""
        dem = _write_dem(tmp_path, _street_dem())
        _s, diag = delineate_junction_subcatchments(_inlets(), AOI, dem_path=dem, config=CFG)
        assert diag["pour_point_snapping"] == {"applied": False}

    def test_a_snap_that_degrades_the_result_is_caught_by_the_posterior_gate(self, tmp_path):
        """Snapping is a heuristic and can make things worse — here both inlets land on the
        same flat gutter and the basins degenerate. The posterior gate (ADR 0010) notices
        and falls back rather than shipping a delineation with holes.

        Pinned because a heuristic without a net is how a plausible-looking bad model gets
        out. Whether the snap moved anything is checked directly in test_inlet_snapping.py;
        what matters here is that a bad outcome cannot survive.
        """
        dem = _write_dem(tmp_path, _street_dem())
        off = {name: _TO_LL(*_utm(col, GUTTER_ROW - 2))
               for name, col in (("CB_W", 30), ("CB_E", 70))}
        subs, diag = delineate_junction_subcatchments(
            off, AOI, dem_path=dem, config=CFG, snap_pour_points=True)
        assert diag["gate"]["decision"] == "posterior_fallback"
        assert diag["method"] == schema.METHOD_JUNCTION_VORONOI
        assert subs, "the fallback still has to produce a delineation"


class TestTheDiagnosticsShapeDoesNotDependOnWhichPathWon:
    """What the conditioning did stays true even when the delineation is thrown away.

    Every fallback inside the DEM path returned a dict without these keys, so a reader had
    to know which branch ran before it could ask a question. That is how this broke in CI
    and not here: the fixture sits on the noise gate's threshold (share exactly 0.5, gate
    fires above 0.5), so a hair of numeric difference between machines flipped the branch
    and four tests died on KeyError rather than on anything about kerbs.

    A diagnostics dict whose shape depends on the outcome cannot be read by a report, a
    test, or a person. The conditioning happened; it is reported either way.
    """

    def _fallback_diag(self, tmp_path):
        dem = _write_dem(tmp_path, _street_dem())
        off = {name: _TO_LL(*_utm(col, GUTTER_ROW - 2))
               for name, col in (("CB_W", 30), ("CB_E", 70))}
        _s, diag = delineate_junction_subcatchments(
            off, AOI, dem_path=dem, config=CFG, snap_pour_points=True)
        assert diag["gate"]["decision"] != "dem", "this scenario is meant to fall back"
        return diag

    def test_a_fallback_still_reports_the_conditioning(self, tmp_path):
        assert "urban_conditioning" in self._fallback_diag(tmp_path)

    def test_a_fallback_still_reports_the_snapping(self, tmp_path):
        diag = self._fallback_diag(tmp_path)
        assert "pour_point_snapping" in diag
        assert diag["pour_point_snapping"]["search_radius_m"] > 0, (
            "the snap ran before the fallback; saying it did not would be a lie")
