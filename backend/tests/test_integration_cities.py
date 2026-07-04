"""Opt-in live end-to-end tests: a real AOI in each real-network city -> a real, re-parseable
SWMM .inp. Hits the city's ArcGIS open data + NRCan MRDEM + ECCC GeoMet. Skipped by default;
run with: pytest -m integration. A 5xx from a city's open-data server (an upstream outage)
skips that city rather than failing — these tests assert OUR pipeline, not the city's uptime.
"""
from datetime import date

import pytest
import requests

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import build_city

# city registry key -> AOI centre; small dense-urban AOIs inside each coverage box.
CITIES = {
    "london": (-81.250, 42.984),
    "kitchener": (-80.490, 43.451),
    "calgary": (-114.075, 51.050),
    "surrey": (-122.845, 49.106),
    "kelowna": (-119.470, 49.884),
}


def _aoi(lon, lat, d=0.0025):
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]]})


@pytest.mark.integration
@pytest.mark.parametrize("city", list(CITIES))
def test_city_builds_real_inp(city, tmp_path):
    lon, lat = CITIES[city]
    try:
        res = build_city(city, _aoi(lon, lat), date(2022, 6, 1), date(2022, 6, 7), tmp_path)
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code and code >= 500:
            pytest.skip(f"{city} open-data server returned {code} (upstream outage)")
        raise

    assert res.inp_path.exists()
    for sec in ("JUNCTIONS", "OUTFALLS", "CONDUITS", "SUBCATCHMENTS", "RAINGAGES", "TIMESERIES"):
        assert sec in res.sections_written
    from swmm_api import read_inp_file

    read_inp_file(str(res.inp_path))   # re-parse proves SWMM-validity
