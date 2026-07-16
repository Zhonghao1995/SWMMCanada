"""CHS/IWLS tide source (#130 gap 3, reframed by ADR 0024 §1): station pick, datum
conversion, clock alignment, chunked predictions, hydraulic selection."""
from datetime import date

import pytest

from swmmcanada.build.models import OutfallIn
from swmmcanada.sources import tides_chs
from swmmcanada.sources.tides_chs import (
    TideStation, fetch_tide_predictions, lst_offset_hours, nearest_tide_station,
    station_datum_offset, tidal_outfall_names)

STATIONS = [
    {"id": "vic", "officialName": "Victoria Harbour", "latitude": 48.4245, "longitude": -123.3707,
     "timeSeries": [{"code": "wlo"}, {"code": "wlp"}]},
    {"id": "obs", "officialName": "Observations Only", "latitude": 48.43, "longitude": -123.37,
     "timeSeries": [{"code": "wlo"}]},
    {"id": "far", "officialName": "Tofino", "latitude": 49.15, "longitude": -125.91,
     "timeSeries": [{"code": "wlp"}]},
]
VIC = TideStation("vic", "Victoria Harbour", 48.4245, -123.3707)


@pytest.fixture(autouse=True)
def _fake_station_list(monkeypatch):
    tides_chs._station_list.cache_clear()
    monkeypatch.setattr(tides_chs, "_station_list", lambda: tuple(STATIONS))


def _fake_get(metadata=None):
    """A _get_json stand-in serving /metadata and /data; records data-call params."""
    calls = []

    def get(url, params=None, timeout=60.0):
        if url.endswith("/metadata"):
            return metadata if metadata is not None else {
                "datums": [{"code": "CGVD2013", "offset": -1.71},
                           {"code": "CGVD28", "offset": -1.87}]}
        calls.append(params)
        from datetime import date as _d, timedelta as _td
        frm = _d.fromisoformat(params["from"][:10])
        to = _d.fromisoformat(params["to"][:10])
        rows = []
        day = frm
        while day < to:
            for h in range(24):                      # dense hourly, like real wlp
                rows.append({"eventDate": f"{day.isoformat()}T{h:02d}:00:00Z",
                             "value": 1.0 if h % 2 == 0 else 2.0})
            day += _td(days=1)
        return rows

    get.calls = calls
    return get


def test_nearest_station_requires_wlp_and_max_km():
    st = nearest_tide_station(48.42, -123.36)
    assert st and st.id == "vic"                       # nearest WITH predictions wins
    assert nearest_tide_station(49.88, -119.47) is None   # Kelowna: nothing within 15 km


def test_datum_offset_prefers_cgvd28(monkeypatch):
    monkeypatch.setattr(tides_chs, "_get_json", _fake_get())
    assert station_datum_offset("vic") == ("CGVD28", -1.87)
    monkeypatch.setattr(tides_chs, "_get_json",
                        _fake_get(metadata={"datums": [{"code": "CGVD2013", "offset": -1.71}]}))
    assert station_datum_offset("vic") == ("CGVD2013", -1.71)


def test_no_datum_conversion_means_no_tide(monkeypatch):
    """ADR 0024 §1: never compare Chart Datum levels to geodetic inverts."""
    monkeypatch.setattr(tides_chs, "_get_json", _fake_get(metadata={"datums": []}))
    with pytest.raises(RuntimeError, match="datum"):
        fetch_tide_predictions(VIC, date(2022, 6, 1), date(2022, 6, 2))


def test_levels_are_datum_shifted_and_clock_aligned(monkeypatch):
    fake = _fake_get()
    monkeypatch.setattr(tides_chs, "_get_json", fake)
    t = fetch_tide_predictions(VIC, date(2022, 6, 1), date(2022, 6, 2))
    assert t.datum == "CGVD28" and t.datum_offset_m == -1.87
    assert t.clock_utc_offset_h == -8.0                 # Victoria: PST
    assert all(v <= 2.0 - 1.87 + 1e-9 for v in t.level_m)   # CD 1.0/2.0 -> geodetic
    # half-open local window, exactly the expected hourly axis (round-2 discipline)
    assert len(t.timestamps) == 48
    assert t.timestamps[0].hour == 0 and t.timestamps[-1].hour == 23
    assert all(p["time-series-code"] == "wlp" for p in fake.calls)


def test_predictions_dedupe_across_chunks(monkeypatch):
    fake = _fake_get()
    monkeypatch.setattr(tides_chs, "_get_json", fake)
    t = fetch_tide_predictions(VIC, date(2022, 6, 1), date(2022, 6, 14))
    assert len(fake.calls) >= 3                         # padded window, 6-day chunks
    assert len(t.timestamps) == len(set(t.timestamps))  # overlap points deduped
    assert t.station_name == "Victoria Harbour"


def test_lst_offsets_cover_canada():
    assert lst_offset_hours(-123.4, 48.4) == -8.0       # Victoria
    assert lst_offset_hours(-75.7, 45.4) == -5.0        # Ottawa
    assert lst_offset_hours(-52.7, 47.6) == -3.5        # St. John's (half-hour zone)
    assert lst_offset_hours(-105.5, 52.1) == -6.0       # Saskatoon: CST year-round
    assert lst_offset_hours(-135.1, 60.7) == -7.0       # Whitehorse: MST year-round


def test_tidal_selection_is_hydraulic_not_geographic():
    outfalls = [OutfallIn("LOW", 0.4, 0, 0), OutfallIn("MID", 2.9, 0, 0),
                OutfallIn("HIGH", 25.0, 0, 0)]
    names = tidal_outfall_names(outfalls, max_level_m=2.64)
    assert names == ["LOW", "MID"]                      # 2.9 <= 2.64+0.5; 25 m is not tidal
