"""A malformed parcel must not take the build down.

Open cadastral data contains self-intersecting rings. Shapely raises a TopologyException
from `intersection` on one, and nothing upstream validated the input — a live downtown AOI
crashed the delineation outright, before any of it reached the model.

The repo learned this once already (geometry validity has to be enforced in the metric CRS,
ADR 0016/0022); the lesson had not been applied to the parcel path.
"""
import pytest

from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.sources.cities import base

AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.372, 48.424], [-123.366, 48.424], [-123.366, 48.428], [-123.372, 48.428],
    [-123.372, 48.424]]]})


def _net():
    return NetworkIn(
        junctions=[JunctionIn("J1", 9.0, -123.3700, 48.4260),
                   JunctionIn("J2", 8.5, -123.3680, 48.4260)],
        outfalls=[OutfallIn("OUT", 7.0, -123.3665, 48.4260)],
        conduits=[ConduitIn("C1", "J1", "J2", 150.0), ConduitIn("C2", "J2", "OUT", 100.0)])


def _cb(name, lon, lat):
    return {"type": "Feature", "properties": {"AssetID": name},
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


def _parcel(ring):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


BOWTIE = [[-123.3710, 48.4250], [-123.3690, 48.4270], [-123.3710, 48.4270],
          [-123.3690, 48.4250], [-123.3710, 48.4250]]      # self-intersecting
GOOD_A = [[-123.3715, 48.4245], [-123.3700, 48.4245], [-123.3700, 48.4258],
          [-123.3715, 48.4258], [-123.3715, 48.4245]]
GOOD_B = [[-123.3695, 48.4245], [-123.3680, 48.4245], [-123.3680, 48.4258],
          [-123.3695, 48.4258], [-123.3695, 48.4245]]


def test_a_self_intersecting_parcel_does_not_crash_the_delineation():
    subs, _imperv, _diag = base.delineate_catchbasin_subcatchments(
        _net(), [_cb("CB1", -123.3705, 48.4252), _cb("CB2", -123.3685, 48.4252)],
        [_parcel(BOWTIE), _parcel(GOOD_A), _parcel(GOOD_B)], [], AOI, crs="EPSG:32610")
    assert subs, "the AOI still has to produce units"


def test_the_valid_parcels_are_still_used():
    """Repairing must not degrade to throwing the whole layer away — a silent fall back to
    Voronoi would look like success and lose the lot lines."""
    subs, _i, diag = base.delineate_catchbasin_subcatchments(
        _net(), [_cb("CB1", -123.3705, 48.4252), _cb("CB2", -123.3685, 48.4252)],
        [_parcel(BOWTIE), _parcel(GOOD_A), _parcel(GOOD_B)], [], AOI, crs="EPSG:32610")
    assert "parcel" in diag["method"], diag["method"]


def test_repairs_are_counted_not_silent():
    """A build that quietly fixed its inputs cannot be audited."""
    _s, _i, diag = base.delineate_catchbasin_subcatchments(
        _net(), [_cb("CB1", -123.3705, 48.4252), _cb("CB2", -123.3685, 48.4252)],
        [_parcel(BOWTIE), _parcel(GOOD_A), _parcel(GOOD_B)], [], AOI, crs="EPSG:32610")
    assert diag.get("n_parcels_repaired", 0) >= 1
