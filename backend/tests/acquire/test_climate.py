"""TDD for acquire.climate (spec 02 §6): offline parse of recorded GeoMet fixtures."""
import math
from datetime import date, datetime

import pandas as pd

from swmmcanada.acquire.climate import (
    ClimateResult,
    ClimateSeries,
    ClimateStation,
    extraterrestrial_radiation,
    fetch_climate,
    hargreaves_pet,
    parse_daily,
    to_evaporation_series,
    to_rainfall_series,
    to_temperature_series,
)
from swmmcanada.geo import aoi_from_geojson

# ~3 km² AOI near Calgary (under the 25 km² cap).
BOX = {
    "type": "Polygon",
    "coordinates": [
        [[-114.02, 51.04], [-114.00, 51.04], [-114.00, 51.06], [-114.02, 51.06], [-114.02, 51.04]]
    ],
}

STATIONS_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-114.01, 51.05]},  # inside AOI
            "properties": {
                "CLIMATE_IDENTIFIER": "3031092", "STATION_NAME": "CALGARY INT'L A",
                "PROV_STATE_TERR_CODE": "AB", "STN_ID": 2205,
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-120.0, 49.0]},  # far outside AOI
            "properties": {"CLIMATE_IDENTIFIER": "9999999", "STATION_NAME": "OUTSIDE", "STN_ID": 1},
        },
    ],
}

DAILY_FC = {
    "type": "FeatureCollection",
    "features": [
        {"properties": {"LOCAL_DATE": "2022-06-01 00:00:00", "TOTAL_PRECIPITATION": 5.2,
                        "MIN_TEMPERATURE": 8.0, "MAX_TEMPERATURE": 18.0, "MEAN_TEMPERATURE": 13.0,
                        "TOTAL_PRECIPITATION_FLAG": None, "CLIMATE_IDENTIFIER": "3031092"}},
        {"properties": {"LOCAL_DATE": "2022-06-02 00:00:00", "TOTAL_PRECIPITATION": 0.0,
                        "MIN_TEMPERATURE": 9.0, "MAX_TEMPERATURE": 20.0, "MEAN_TEMPERATURE": 14.5,
                        "TOTAL_PRECIPITATION_FLAG": None, "CLIMATE_IDENTIFIER": "3031092"}},
        {"properties": {"LOCAL_DATE": "2022-06-03 00:00:00", "TOTAL_PRECIPITATION": None,
                        "MIN_TEMPERATURE": 7.0, "MAX_TEMPERATURE": 16.0, "MEAN_TEMPERATURE": 11.5,
                        "TOTAL_PRECIPITATION_FLAG": "T", "CLIMATE_IDENTIFIER": "3031092"}},
    ],
}


class FakeClient:
    def get_json(self, url, params):
        if "climate-stations" in url:
            return STATIONS_FC
        if "climate-daily" in url:
            return DAILY_FC
        return {"features": []}


def test_fetch_climate_selects_inside_and_parses():
    aoi = aoi_from_geojson(BOX)
    res = fetch_climate(aoi, date(2022, 6, 1), date(2022, 6, 3), client=FakeClient())
    assert isinstance(res, ClimateResult)
    assert [s.climate_id for s in res.stations] == ["3031092"]  # outside station excluded
    assert len(res.series) == 1
    df = res.series[0].frame
    assert df.shape[0] == 3
    assert list(df["precip_mm"]) == [5.2, 0.0, 0.0]            # trace 'T' → 0
    assert {"timestamp_local", "precip_mm", "tmin_c", "tmax_c", "tmean_c"}.issubset(df.columns)


# A dense cluster of discontinued stations nearer than the one continuous station —
# the real failure mode near Victoria/Saanich that the Tod Creek boundary surfaced.
def _station(cid, lon, lat):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"CLIMATE_IDENTIFIER": cid, "STATION_NAME": cid, "STN_ID": 0},
    }


CLUSTER_FC = {
    "type": "FeatureCollection",
    # 15 dead stations just outside the AOI (closer to centroid) + 1 good station farther out.
    "features": [_station(f"DEAD{i:02d}", -114.03 - 0.001 * i, 51.05) for i in range(15)]
    + [_station("GOOD", -114.06, 51.05)],
}


class ClusterClient:
    """Only GOOD returns daily data; every DEAD station is empty (discontinued)."""

    def get_json(self, url, params):
        if "climate-stations" in url:
            return CLUSTER_FC
        if "climate-daily" in url:
            return DAILY_FC if params.get("CLIMATE_IDENTIFIER") == "GOOD" else {"features": []}
        return {"features": []}


def test_fetch_climate_reaches_good_station_past_dead_cluster():
    # No station inside; the nearest 15 are dead. The fallback must keep going to GOOD.
    aoi = aoi_from_geojson(BOX)
    res = fetch_climate(aoi, date(2022, 6, 1), date(2022, 6, 3), client=ClusterClient())
    assert [s.climate_id for s in res.stations] == ["GOOD"]
    assert len(res.series) == 1 and not res.series[0].frame.empty


def test_to_rainfall_series_feeds_build():
    aoi = aoi_from_geojson(BOX)
    res = fetch_climate(aoi, date(2022, 6, 1), date(2022, 6, 3), client=FakeClient())
    rain = to_rainfall_series(res.series[0])
    assert len(rain.timestamps) == 3
    assert rain.precip_mm[0] == 5.2
    assert isinstance(rain.timestamps[0], datetime)


# A non-empty but precip-less station (e.g. temperature-only / gappy) must be skipped:
# it would otherwise feed an all-NaN raingage and NaN the whole SWMM run (Victoria downtown).
NAN_PRECIP_DAILY = {
    "type": "FeatureCollection",
    "features": [
        {"properties": {"LOCAL_DATE": "2022-06-01 00:00:00", "TOTAL_PRECIPITATION": None,
                        "MIN_TEMPERATURE": 8.0, "MAX_TEMPERATURE": 18.0, "MEAN_TEMPERATURE": 13.0,
                        "TOTAL_PRECIPITATION_FLAG": None, "CLIMATE_IDENTIFIER": "NANSTN"}},
    ],
}

PRECIP_STATIONS_FC = {
    "type": "FeatureCollection",
    "features": [
        _station("NANSTN", -114.012, 51.05),   # closest to the AOI centroid, but precip-less
        _station("GOOD", -114.06, 51.05),        # farther, has real precip (DAILY_FC)
    ],
}


class PrecipSelectClient:
    def get_json(self, url, params):
        if "climate-stations" in url:
            return PRECIP_STATIONS_FC
        if "climate-daily" in url:
            return NAN_PRECIP_DAILY if params.get("CLIMATE_IDENTIFIER") == "NANSTN" else DAILY_FC
        return {"features": []}


def test_fetch_climate_skips_precipless_station():
    aoi = aoi_from_geojson(BOX)
    res = fetch_climate(aoi, date(2022, 6, 1), date(2022, 6, 3), client=PrecipSelectClient())
    assert [s.climate_id for s in res.stations] == ["GOOD"]            # not the closer NaN station
    assert res.series and bool(res.series[0].frame["precip_mm"].notna().any())


# Two usable stations, both outside the AOI: the NEARER one must win (nearest-with-usable-precip).
NEAREST_STATIONS_FC = {
    "type": "FeatureCollection",
    "features": [_station("NEAR", -114.05, 51.05), _station("FAR", -114.30, 51.05)],
}


class NearestClient:
    def get_json(self, url, params):
        if "climate-stations" in url:
            return NEAREST_STATIONS_FC
        if "climate-daily" in url:
            return DAILY_FC                                            # both stations have data
        return {"features": []}


def test_fetch_climate_picks_nearest_usable_station():
    aoi = aoi_from_geojson(BOX)
    res = fetch_climate(aoi, date(2022, 6, 1), date(2022, 6, 3), client=NearestClient())
    assert [s.climate_id for s in res.stations] == ["NEAR"]            # nearer of two usable stations


def test_to_rainfall_series_coerces_nan_to_zero():
    fc = {"type": "FeatureCollection", "features": [
        {"properties": {"LOCAL_DATE": "2022-06-01 00:00:00", "TOTAL_PRECIPITATION": 3.0,
                        "TOTAL_PRECIPITATION_FLAG": None, "CLIMATE_IDENTIFIER": "X"}},
        {"properties": {"LOCAL_DATE": "2022-06-02 00:00:00", "TOTAL_PRECIPITATION": None,
                        "TOTAL_PRECIPITATION_FLAG": None, "CLIMATE_IDENTIFIER": "X"}},
    ]}
    rain = to_rainfall_series(ClimateSeries(station=None, frame=parse_daily(fc)))
    assert rain.precip_mm == [3.0, 0.0]                                # NaN gap -> 0 mm, not NaN


# --- evaporation forcing (Hargreaves) ----------------------------------------


def test_extraterrestrial_radiation_matches_fao56():
    # FAO-56 Example 8: lat -20°, 3 Sep (doy 246) → Ra ≈ 32.2 MJ m-2 day-1.
    assert math.isclose(extraterrestrial_radiation(-20.0, 246), 32.2, abs_tol=0.3)


def test_hargreaves_summer_is_physical_and_cold_clamps_to_zero():
    summer = hargreaves_pet(8.0, 18.0, 13.0, 51.05, 152)   # Calgary, ~Jun 1
    assert 2.0 < summer < 6.0                                # a few mm/day in summer
    winter = hargreaves_pet(-20.0, -8.0, -14.0, 51.05, 1)   # sub-freezing → non-negative
    assert winter >= 0.0


def test_to_evaporation_series_uses_station_lat_and_skips_missing_temps():
    fc = {"type": "FeatureCollection", "features": [
        {"properties": {"LOCAL_DATE": "2022-06-01 00:00:00", "TOTAL_PRECIPITATION": 0.0,
                        "MIN_TEMPERATURE": 8.0, "MAX_TEMPERATURE": 18.0, "MEAN_TEMPERATURE": 13.0,
                        "CLIMATE_IDENTIFIER": "X"}},
        {"properties": {"LOCAL_DATE": "2022-06-02 00:00:00", "TOTAL_PRECIPITATION": 0.0,
                        "MIN_TEMPERATURE": None, "MAX_TEMPERATURE": 20.0, "MEAN_TEMPERATURE": 15.0,
                        "CLIMATE_IDENTIFIER": "X"}},  # missing tmin → skipped
    ]}
    station = ClimateStation(climate_id="X", name="X", lon=-114.01, lat=51.05)
    evap = to_evaporation_series(ClimateSeries(station=station, frame=parse_daily(fc)))
    assert evap is not None
    assert len(evap.timestamps) == 1 and evap.timestamps[0] == datetime(2022, 6, 1)
    assert 2.0 < evap.evap_mm_day[0] < 6.0
    assert evap.ts_name == "evap"


def test_to_evaporation_series_none_without_latitude():
    fc = {"type": "FeatureCollection", "features": [
        {"properties": {"LOCAL_DATE": "2022-06-01 00:00:00", "TOTAL_PRECIPITATION": 0.0,
                        "MIN_TEMPERATURE": 8.0, "MAX_TEMPERATURE": 18.0, "MEAN_TEMPERATURE": 13.0,
                        "CLIMATE_IDENTIFIER": "X"}},
    ]}
    assert to_evaporation_series(ClimateSeries(station=None, frame=parse_daily(fc))) is None


def test_to_temperature_series_falls_back_to_midpoint():
    fc = {"type": "FeatureCollection", "features": [
        {"properties": {"LOCAL_DATE": "2022-06-01 00:00:00", "TOTAL_PRECIPITATION": 0.0,
                        "MIN_TEMPERATURE": 8.0, "MAX_TEMPERATURE": 18.0, "MEAN_TEMPERATURE": None,
                        "CLIMATE_IDENTIFIER": "X"}},
    ]}
    temp = to_temperature_series(ClimateSeries(station=None, frame=parse_daily(fc)))
    assert temp is not None
    assert temp.tmean_c == [13.0]                                       # (8 + 18) / 2


# --- hourly rainfall tier (ADR 0014) --------------------------------------------------

def _hourly_fc(n_hours, precip_each=0.5, station="3031092", missing_every=None):
    """Fixture: n_hours of hourly records starting 2022-06-01 00:00; every `missing_every`-th
    record has PRECIP_AMOUNT=None (a gap hour)."""
    feats = []
    t0 = datetime(2022, 6, 1, 0)
    for i in range(n_hours):
        t = t0 + pd.Timedelta(hours=i)
        val = None if (missing_every and i % missing_every == 0) else precip_each
        feats.append({"properties": {
            "LOCAL_DATE": t.strftime("%Y-%m-%d %H:%M:%S"),
            "PRECIP_AMOUNT": val, "PRECIP_AMOUNT_FLAG": None,
            "CLIMATE_IDENTIFIER": station}})
    return {"type": "FeatureCollection", "features": feats}


class HourlyClient(FakeClient):
    def __init__(self, hourly_fc):
        self._hourly = hourly_fc

    def get_json(self, url, params):
        if "climate-hourly" in url:
            return self._hourly
        return super().get_json(url, params)


def _aoi():
    return aoi_from_geojson(BOX)


def test_hourly_tier_selected_when_coverage_is_good():
    # 2 days requested = 48 expected hours; fixture provides all 48 -> hourly wins.
    res = fetch_climate(_aoi(), date(2022, 6, 1), date(2022, 6, 2),
                        client=HourlyClient(_hourly_fc(48)))
    assert res.hourly_rain is not None
    f = res.forcing
    assert f["rainfall_resolution"] == "hourly"
    assert f["coverage_pct"] == 100.0 and f["n_missing_hours"] == 0
    assert f["hourly_total_mm"] == 24.0                      # 48 x 0.5 mm
    rain = to_rainfall_series(res.hourly_rain)
    assert len(rain.timestamps) == 48
    assert (rain.timestamps[1] - rain.timestamps[0]).seconds == 3600


def test_hourly_below_coverage_falls_back_to_daily():
    # every 3rd hour missing -> ~67% coverage < 90% -> daily fallback, reason recorded.
    res = fetch_climate(_aoi(), date(2022, 6, 1), date(2022, 6, 2),
                        client=HourlyClient(_hourly_fc(48, missing_every=3)))
    assert res.hourly_rain is None
    assert res.forcing["rainfall_resolution"] == "daily"
    assert "coverage" in res.forcing["fallback_reason"] or ">=90%" in res.forcing["fallback_reason"]


def test_no_hourly_records_falls_back_to_daily():
    res = fetch_climate(_aoi(), date(2022, 6, 1), date(2022, 6, 2), client=FakeClient())
    assert res.hourly_rain is None and res.forcing["rainfall_resolution"] == "daily"


def test_mismatch_beyond_tolerance_warns():
    # hourly total = 48 x 1.0 = 48 mm vs the daily fixture total 5.2 mm -> big mismatch.
    res = fetch_climate(_aoi(), date(2022, 6, 1), date(2022, 6, 2),
                        client=HourlyClient(_hourly_fc(48, precip_each=1.0)))
    assert res.forcing["mismatch_pct"] > 15.0
    assert any("differs from the daily" in w for w in res.warnings)


def test_missing_hours_within_tolerance_count_as_zero_rain():
    # every 20th hour missing -> 95% coverage passes; missing hours become 0 in the series.
    res = fetch_climate(_aoi(), date(2022, 6, 1), date(2022, 6, 2),
                        client=HourlyClient(_hourly_fc(48, missing_every=20)))
    assert res.forcing["rainfall_resolution"] == "hourly"
    assert res.forcing["n_missing_hours"] == 3               # hours 0, 20, 40
    rain = to_rainfall_series(res.hourly_rain)
    assert rain.precip_mm[0] == 0.0                          # NaN -> 0, recorded not hidden
