"""Opt-in live end-to-end test: a real Ottawa AOI → a real, round-trippable SWMM .inp.
Hits OSM (Overpass), NRCan MRDEM (S3 COG), and ECCC GeoMet. Skipped by default; run with:
    pytest -m integration
"""
from datetime import date

import pytest

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import build_from_aoi

# ~1.7 km² of central Ottawa (good OSM street coverage), under the 25 km² cap.
OTTAWA = {
    "type": "Polygon",
    "coordinates": [
        [[-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]
    ],
}


@pytest.mark.integration
def test_end_to_end_real_aoi(tmp_path):
    aoi = aoi_from_geojson(OTTAWA)
    res = build_from_aoi(aoi, date(2022, 6, 1), date(2022, 6, 7), tmp_path)

    assert res.inp_path.exists()                       # build already round-tripped it
    for sec in ("JUNCTIONS", "OUTFALLS", "CONDUITS", "SUBCATCHMENTS", "RAINGAGES", "TIMESERIES"):
        assert sec in res.sections_written
