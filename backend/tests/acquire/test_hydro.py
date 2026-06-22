"""TDD for acquire.hydro (spec 03 §6): melt a tiny HYDAT SQLite fixture into tidy daily flow."""
import sqlite3
from datetime import date

from swmmcanada.acquire.hydro import HydroResult, fetch_hydro
from swmmcanada.geo import aoi_from_geojson

# ~3 km² AOI near Ottawa.
BOX = {
    "type": "Polygon",
    "coordinates": [
        [[-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.43], [-75.70, 45.43], [-75.70, 45.41]]
    ],
}


def _make_hydat(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE STATIONS (STATION_NUMBER TEXT, STATION_NAME TEXT, LATITUDE REAL, "
        "LONGITUDE REAL, DRAINAGE_AREA_GROSS REAL)"
    )
    conn.executemany(
        "INSERT INTO STATIONS VALUES (?,?,?,?,?)",
        [
            ("02LA004", "RIDEAU RIVER", 45.42, -75.69, 1490.0),  # inside AOI
            ("99XX999", "FAR AWAY", 49.0, -120.0, 10.0),          # outside AOI
        ],
    )
    flow_cols = ", ".join(f"FLOW{i} REAL" for i in range(1, 32))
    sym_cols = ", ".join(f"FLOW_SYMBOL{i} TEXT" for i in range(1, 32))
    conn.execute(f"CREATE TABLE DLY_FLOWS (STATION_NUMBER TEXT, YEAR INT, MONTH INT, {flow_cols}, {sym_cols})")
    # June 2022 row: day1=12.5, day2=13.0, day3=NULL (gap), rest NULL.
    flows = [None] * 31
    flows[0], flows[1] = 12.5, 13.0
    syms = [None] * 31
    syms[1] = "B"  # ice flag on day 2
    conn.execute(
        "INSERT INTO DLY_FLOWS VALUES (" + ",".join(["?"] * (3 + 62)) + ")",
        ["02LA004", 2022, 6] + flows + syms,
    )
    conn.commit()
    conn.close()


def test_fetch_hydro_selects_inside_and_melts(tmp_path):
    hydat = tmp_path / "hydat.sqlite"
    _make_hydat(hydat)
    aoi = aoi_from_geojson(BOX)
    res = fetch_hydro(aoi, date(2022, 6, 1), date(2022, 6, 30), hydat_path=str(hydat))

    assert isinstance(res, HydroResult)
    assert [s.station_number for s in res.stations] == ["02LA004"]   # outside station excluded
    df = res.flows
    assert list(df["date"]) == [date(2022, 6, 1), date(2022, 6, 2)]  # day3 NULL skipped
    assert list(df["discharge_cms"]) == [12.5, 13.0]
    assert df.loc[df["date"] == date(2022, 6, 2), "symbol"].iloc[0] == "B"  # ice flag surfaced


def test_date_range_filters(tmp_path):
    hydat = tmp_path / "hydat.sqlite"
    _make_hydat(hydat)
    aoi = aoi_from_geojson(BOX)
    res = fetch_hydro(aoi, date(2022, 6, 2), date(2022, 6, 2), hydat_path=str(hydat))
    assert list(res.flows["date"]) == [date(2022, 6, 2)]
    assert res.source_coverage["02LA004"] == 1
