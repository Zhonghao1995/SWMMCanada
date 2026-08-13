"""The pipeline's validation gate: validation.json is always written into the package, and
an error-severity model stops the build (PRD: subcatchment validation)."""
import json

import pytest

from swmmcanada.build.models import JunctionIn, NetworkIn, SurfaceCatchment
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import _method_descriptor, _validate_or_raise
from swmmcanada.validate import MethodDescriptor, SubcatchmentValidationError
from swmmcanada.validate import schema

AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.372, 48.418], [-123.368, 48.418], [-123.368, 48.422], [-123.372, 48.422], [-123.372, 48.418]]]})
NET = NetworkIn(junctions=[JunctionIn("J1", 10.0, -123.371, 48.420)], outfalls=[], conduits=[])
MD = MethodDescriptor(schema.METHOD_CATCHBASIN_VORONOI, "nearest inlet service area", "low")
FULL = [(-123.372, 48.418), (-123.368, 48.418), (-123.368, 48.422), (-123.372, 48.422)]
LEFT = [(-123.372, 48.418), (-123.370, 48.418), (-123.370, 48.422), (-123.372, 48.422)]


def _sub(ring):
    return SurfaceCatchment("S", "J1", area_ha=AOI.area_km2 * 100, pct_imperv=50.0,
                          width_m=100.0, pct_slope=1.0, polygon=ring)


def test_clean_model_writes_validation_json_and_passes(tmp_path):
    report = _validate_or_raise(NET, [_sub(FULL)], AOI, MD, tmp_path)
    assert report.ok
    meta = json.loads((tmp_path / "validation.json").read_text())
    assert meta["ok"] is True and meta["subcatchment_method"] == schema.METHOD_CATCHBASIN_VORONOI


def test_error_model_writes_validation_json_then_raises(tmp_path):
    with pytest.raises(SubcatchmentValidationError):
        _validate_or_raise(NET, [_sub(LEFT)], AOI, MD, tmp_path)        # right half is a blank hole
    meta = json.loads((tmp_path / "validation.json").read_text())       # still written -> explains it
    assert meta["ok"] is False
    assert any(c["id"] == "aoi_coverage" and not c["passed"] for c in meta["checks"])


def test_method_descriptor_mapping():
    assert _method_descriptor({"method": "catchbasin+parcel/building (parcel-shaped)"}).method == "catchbasin_parcel"
    assert _method_descriptor({"method": "catchbasin+parcel/building (voronoi-shaped)"}).method == schema.METHOD_CATCHBASIN_VORONOI
    assert _method_descriptor({"method": "voronoi-of-nodes"}).method == schema.METHOD_JUNCTION_VORONOI
    assert _method_descriptor(None).method == schema.METHOD_JUNCTION_VORONOI
