"""acquire.hydro (spec 03): AOI + date range → observed daily streamflow (m³/s) for WSC
hydrometric stations, from the offline HYDAT SQLite full database. This is a calibration
TARGET, not a SWMM forcing — it is never written into the `.inp`.

The real work is melting HYDAT's wide monthly layout (one row per station-year-month with
FLOW1..FLOW31 columns) into a tidy daily long series. Fully offline (stdlib sqlite3).
"""
import calendar
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

import pandas as pd
from shapely.geometry import Point

_FLOW_COLS = [f"FLOW{i}" for i in range(1, 32)]
_SYM_COLS = [f"FLOW_SYMBOL{i}" for i in range(1, 32)]
_OUT_COLS = ["station_number", "date", "discharge_cms", "symbol", "source"]


@dataclass(frozen=True)
class HydroStation:
    station_number: str
    name: Optional[str]
    lon: float
    lat: float
    drainage_area_km2: Optional[float] = None


@dataclass(frozen=True)
class HydroResult:
    stations: List[HydroStation]
    flows: pd.DataFrame                       # tidy long: station_number, date, discharge_cms, symbol, source
    source_coverage: dict = field(default_factory=dict)
    snapshot_date: Optional[date] = None


def fetch_hydro(aoi, start: date, end: date, *, hydat_path: "str") -> HydroResult:
    """aoi: a geo.AOI. Selects hydrometric stations inside the AOI polygon and reads each
    one's daily mean flow for [start, end] from the HYDAT SQLite."""
    conn = sqlite3.connect(str(hydat_path))
    try:
        stations = _read_stations(conn, aoi.bbox)
        inside = [s for s in stations if aoi.geometry.contains(Point(s.lon, s.lat))]
        rows: List[Tuple] = []
        coverage = {}
        for s in inside:
            station_rows = _read_daily_flows(conn, s.station_number, start, end)
            rows.extend(station_rows)
            coverage[s.station_number] = len(station_rows)
    finally:
        conn.close()

    flows = pd.DataFrame(rows, columns=_OUT_COLS)
    if not flows.empty:
        flows = flows.sort_values(["station_number", "date"]).reset_index(drop=True)
    return HydroResult(stations=inside, flows=flows, source_coverage=coverage)


# --- internals ---------------------------------------------------------------


def _read_stations(conn: sqlite3.Connection, bbox) -> List[HydroStation]:
    minlon, minlat, maxlon, maxlat = bbox
    cur = conn.execute(
        "SELECT STATION_NUMBER, STATION_NAME, LATITUDE, LONGITUDE, DRAINAGE_AREA_GROSS "
        "FROM STATIONS WHERE LONGITUDE BETWEEN ? AND ? AND LATITUDE BETWEEN ? AND ?",
        (minlon, maxlon, minlat, maxlat),
    )
    out = []
    for stn, name, lat, lon, area in cur.fetchall():
        out.append(
            HydroStation(
                station_number=stn,
                name=name,
                lon=float(lon),
                lat=float(lat),
                drainage_area_km2=(float(area) if area is not None else None),
            )
        )
    return out


def _read_daily_flows(conn: sqlite3.Connection, station_number: str, start: date, end: date) -> List[Tuple]:
    cols = ["YEAR", "MONTH"] + _FLOW_COLS + _SYM_COLS
    sql = (
        f"SELECT {','.join(cols)} FROM DLY_FLOWS "
        "WHERE STATION_NUMBER = ? AND (YEAR * 100 + MONTH) BETWEEN ? AND ?"
    )
    ym_start = start.year * 100 + start.month
    ym_end = end.year * 100 + end.month
    rows: List[Tuple] = []
    for rec in conn.execute(sql, (station_number, ym_start, ym_end)).fetchall():
        year, month = int(rec[0]), int(rec[1])
        flows = rec[2 : 2 + 31]
        symbols = rec[2 + 31 : 2 + 62]
        days = calendar.monthrange(year, month)[1]
        for d in range(1, days + 1):
            value = flows[d - 1]
            if value is None:
                continue
            day = date(year, month, d)
            if start <= day <= end:
                rows.append((station_number, day, float(value), symbols[d - 1], "hydat"))
    return rows
