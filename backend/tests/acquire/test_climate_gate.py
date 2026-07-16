"""F-001 / ADR 0024 §2: the daily completeness gate — one valid day in a long window must
never pass, gaps are bounded, pagination concatenates, and the forcing record carries the
evidence."""
from datetime import date, timedelta

from swmmcanada.acquire.climate import (
    DAILY_COVERAGE_MIN, daily_completeness, fetch_climate, parse_daily,
)
from swmmcanada.geo import aoi_from_geojson

BOX = {"type": "Polygon", "coordinates": [[
    [-123.40, 48.40], [-123.355, 48.40], [-123.355, 48.44], [-123.40, 48.44], [-123.40, 48.40]]]}
AOI = aoi_from_geojson(BOX)

STATIONS_FC = {"features": [
    {"properties": {"CLIMATE_IDENTIFIER": "GOOD", "STATION_NAME": "Good"},
     "geometry": {"type": "Point", "coordinates": [-123.37, 48.42]}},
    {"properties": {"CLIMATE_IDENTIFIER": "SPARSE", "STATION_NAME": "Sparse"},
     "geometry": {"type": "Point", "coordinates": [-123.375, 48.415]}},
]}


def _daily_fc(climate_id, days):
    """days: list of (date, precip or None)."""
    return {"features": [
        {"properties": {"CLIMATE_IDENTIFIER": climate_id,
                        "LOCAL_DATE": d.isoformat(),
                        "TOTAL_PRECIPITATION": v}}
        for d, v in days]}


class GateClient:
    """SPARSE has one valid day in the window; GOOD covers every day."""

    def __init__(self, start, end):
        self.start, self.end = start, end

    def get_json(self, url, params):
        if "climate-stations" in url:
            return STATIONS_FC
        if "climate-daily" in url:
            cid = params["CLIMATE_IDENTIFIER"]
            if cid == "SPARSE":
                return _daily_fc(cid, [(self.start, 4.0)])
            days = []
            d = self.start
            while d <= self.end:
                days.append((d, 1.0))
                d += timedelta(days=1)
            return _daily_fc(cid, days)
        return {"features": []}


def test_one_valid_day_never_passes_the_gate():
    start, end = date(2021, 1, 1), date(2021, 12, 31)
    comp = daily_completeness(
        parse_daily(_daily_fc("SPARSE", [(start, 4.0)])), start, end)
    assert comp["coverage"] < 0.01 and comp["n_missing"] == 364
    assert comp["coverage"] < DAILY_COVERAGE_MIN


def test_gap_structure_is_measured():
    start, end = date(2022, 6, 1), date(2022, 6, 30)
    days = [(start + timedelta(days=i), 1.0) for i in range(30) if not 10 <= i < 15]
    comp = daily_completeness(parse_daily(_daily_fc("X", days)), start, end)
    assert comp["n_missing"] == 5 and comp["max_gap_d"] == 5
    assert abs(comp["coverage"] - 25 / 30) < 1e-9


def test_selection_skips_the_sparse_station():
    start, end = date(2021, 1, 1), date(2021, 12, 31)
    res = fetch_climate(AOI, start, end, client=GateClient(start, end))
    assert [s.climate_id for s in res.stations] == ["GOOD"]
    assert res.forcing["daily_station"] == "GOOD"
    assert res.forcing["daily_coverage_pct"] == 100.0
    assert res.forcing["n_days_zero_filled"] == 0


class OnlySparseClient(GateClient):
    def get_json(self, url, params):
        if "climate-stations" in url:
            return {"features": STATIONS_FC["features"][1:]}   # SPARSE only
        return super().get_json(url, params)


def test_no_usable_station_is_reported_not_zero_filled():
    start, end = date(2021, 1, 1), date(2021, 12, 31)
    res = fetch_climate(AOI, start, end, client=OnlySparseClient(start, end))
    assert res.stations == [] and res.series == []
    assert any("completeness gate" in w for w in res.warnings)


class PagingClient:
    """climate-daily served in 3 pages of 2 rows: pagination must concatenate."""

    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        if "climate-stations" in url:
            return {"features": STATIONS_FC["features"][:1]}
        if "climate-daily" in url:
            self.calls.append(int(params.get("startindex", 0)))
            all_days = [(date(2022, 6, 1) + timedelta(days=i), 1.0) for i in range(6)]
            i = int(params.get("startindex", 0))
            page = all_days[i: i + int(params["limit"])]
            return _daily_fc("GOOD", page)
        return {"features": []}


def test_pagination_concatenates(monkeypatch):
    import swmmcanada.acquire.climate as climate
    client = PagingClient()
    frame = climate._fetch_daily.__wrapped__(client, "GOOD", date(2022, 6, 1), date(2022, 6, 6)) \
        if hasattr(climate._fetch_daily, "__wrapped__") else \
        climate.parse_daily(climate._fetch_all_pages(client, "climate-daily", "GOOD",
                                                     date(2022, 6, 1), date(2022, 6, 6),
                                                     page_size=2))
    assert frame.shape[0] == 6
    assert client.calls == [0, 2, 4, 6]   # trailing empty page confirms the end


# --- round-2 counterexamples pinned as regressions -------------------------------------

def test_missing_day_is_actually_zero_filled_in_output():
    """Round-2 repro 1: 10 days with 1 wholly-missing DATE passed the gate but shipped
    only 9 points. Reindexing onto the expected axis makes the output exactly 10 points
    with an explicit 0.0 on the missing day."""
    from swmmcanada.acquire.climate import (
        ClimateSeries, ClimateStation, reindex_daily, to_rainfall_series)

    start, end = date(2022, 6, 1), date(2022, 6, 10)
    days = [(start + timedelta(days=i), 1.0) for i in range(10) if i != 4]
    frame = reindex_daily(parse_daily(_daily_fc("X", days)), start, end)
    rain = to_rainfall_series(ClimateSeries(
        station=ClimateStation("X", "X", -123.37, 48.42), frame=frame))
    assert len(rain.timestamps) == 10
    assert rain.precip_mm[4] == 0.0 and sum(rain.precip_mm) == 9.0


def test_72h_hole_at_90pct_coverage_is_rejected():
    """Round-2 repro 2: coverage alone let a 72 h hole through; the max-gap rule kills it."""
    from swmmcanada.acquire.climate import HOURLY_MAX_GAP_H, hourly_completeness
    import pandas as pd

    start, end = date(2022, 6, 1), date(2022, 6, 30)
    axis = pd.date_range(start, end + timedelta(days=1), freq="h", inclusive="left")
    rows = [{"eventDate": t.isoformat(), "value": 1.0}
            for i, t in enumerate(axis) if not 100 <= i < 172]        # 72 h hole
    frame = pd.DataFrame({"timestamp_local": [pd.Timestamp(r["eventDate"]) for r in rows],
                          "precip_mm": [r["value"] for r in rows]})
    comp = hourly_completeness(frame, start, end)
    assert comp["coverage"] >= 0.90                     # coverage alone would pass
    assert comp["max_gap_h"] == 72 > HOURLY_MAX_GAP_H   # the gap rule rejects


def test_duplicated_hours_do_not_inflate_coverage():
    """Round-2 repro: the same hour repeated 24x must count once, not as a full day."""
    from swmmcanada.acquire.climate import hourly_completeness
    import pandas as pd

    start = end = date(2022, 6, 1)
    frame = pd.DataFrame({
        "timestamp_local": [pd.Timestamp("2022-06-01T08:00:00")] * 24,
        "precip_mm": [1.0] * 24})
    comp = hourly_completeness(frame, start, end)
    assert comp["n_duplicates"] == 23
    assert comp["coverage"] < 0.05                      # one real hour of 24
