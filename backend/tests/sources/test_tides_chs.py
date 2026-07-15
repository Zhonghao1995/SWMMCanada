"""CHS/IWLS tide source (#130 gap 3): station pick, chunked predictions, tidal selection."""
from datetime import date

import pytest

from swmmcanada.build.models import OutfallIn
from swmmcanada.sources import tides_chs
from swmmcanada.sources.tides_chs import (
    TideStation, fetch_tide_predictions, nearest_tide_station, tidal_outfall_names)

STATIONS = [
    {"id": "vic", "officialName": "Victoria Harbour", "latitude": 48.4245, "longitude": -123.3707,
     "timeSeries": [{"code": "wlo"}, {"code": "wlp"}]},
    {"id": "obs", "officialName": "Observations Only", "latitude": 48.43, "longitude": -123.37,
     "timeSeries": [{"code": "wlo"}]},
    {"id": "far", "officialName": "Tofino", "latitude": 49.15, "longitude": -125.91,
     "timeSeries": [{"code": "wlp"}]},
]


@pytest.fixture(autouse=True)
def _fake_station_list(monkeypatch):
    tides_chs._station_list.cache_clear()
    monkeypatch.setattr(tides_chs, "_station_list", lambda: tuple(STATIONS))


def test_nearest_station_requires_wlp_and_max_km():
    st = nearest_tide_station(48.42, -123.36)
    assert st and st.id == "vic"                       # nearest WITH predictions wins
    assert nearest_tide_station(49.88, -119.47) is None   # Kelowna: nothing within 15 km


def test_predictions_chunk_and_dedupe(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=60.0):
        calls.append(params)
        frm = params["from"][:10]
        return [{"eventDate": f"{frm}T00:00:00Z", "value": 1.0},
                {"eventDate": f"{frm}T01:00:00Z", "value": 2.0}]

    monkeypatch.setattr(tides_chs, "_get_json", fake_get)
    st = TideStation("vic", "Victoria Harbour", 48.42, -123.37)
    t = fetch_tide_predictions(st, date(2022, 6, 1), date(2022, 6, 14))
    assert len(calls) == 3                              # 14+1 days in 6-day chunks
    assert all(p["time-series-code"] == "wlp" for p in calls)
    assert len(t.timestamps) == len(set(t.timestamps))  # overlap points deduped
    assert t.station_name == "Victoria Harbour"


def test_tidal_selection_is_hydraulic_not_geographic():
    outfalls = [OutfallIn("LOW", 0.4, 0, 0), OutfallIn("MID", 2.9, 0, 0),
                OutfallIn("HIGH", 25.0, 0, 0)]
    names = tidal_outfall_names(outfalls, max_level_m=2.64)
    assert names == ["LOW", "MID"]                      # 2.9 <= 2.64+0.5; 25 m is not tidal
