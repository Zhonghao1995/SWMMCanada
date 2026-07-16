"""CHS/DFO tide predictions (IWLS public API) — coastal outfall boundaries (#130 gap 3).

The Canadian Hydrographic Service's Integrated Water Level System serves every CHS station
with predicted water levels (``wlp`` time-series code) as public JSON. A coastal AOI gets
the nearest wlp-capable station within ``max_km``; predictions are fetched in <=6-day
chunks (the API caps one data request at 7 days) at hourly resolution and become the
``TIMESERIES`` stage boundary for tide-affected outfalls.

Verified live 2026-07-15: Victoria Harbour (5cebf1df3d0f4a073c4bbd1e) 0.6 km from the
paper AOI, hourly wlp for 2022-06-01 spanning 0.30-2.64 m CD.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, datetime, timedelta
from typing import List, Optional

from swmmcanada.build.models import TideSeries
from swmmcanada.sources import _http

IWLS = "https://api-iwls.dfo-mpo.gc.ca/api/v1"
_CHUNK_DAYS = 6
_RESOLUTION = "SIXTY_MINUTES"
# Municipal as-built inverts are overwhelmingly CGVD28 (older vertical control); CGVD2013
# is the fallback when a station lacks the CGVD28 offset. ADR 0024 §1.
_DATUM_PREFERENCE = ("CGVD28", "CGVD2013")


@dataclass(frozen=True)
class TideStation:
    id: str
    name: str
    lat: float
    lon: float


def station_datum_offset(station_id: str) -> Optional[tuple]:
    """(datum_code, offset_m) converting Chart Datum to a geodetic datum, from the CHS
    station metadata (``datums`` carries e.g. CGVD28: -1.87 for Victoria Harbour), or
    None when the station publishes no usable conversion — in which case the tide
    boundary must stay OFF (ADR 0024 §1: never compare CD levels to CGVD inverts)."""
    meta = _get_json(f"{IWLS}/stations/{station_id}/metadata") or {}
    offsets = {str(d.get("code")): d.get("offset") for d in (meta.get("datums") or [])
               if d.get("offset") is not None}
    for code in _DATUM_PREFERENCE:
        if code in offsets:
            return code, float(offsets[code])
    return None


def lst_offset_hours(lon: float, lat: float) -> float:
    """Canada local STANDARD time offset from UTC for a coordinate. Longitude/15 rounding
    lands every mainland zone (PST -8 ... AST -4); Newfoundland's half-hour zone is the
    one exception worth special-casing. No DST — ECCC LOCAL_DATE is standard time."""
    if lon > -59.0 and lat > 46.5:      # island of Newfoundland
        return -3.5
    return float(round(lon / 15.0))


def _get_json(url: str, params: Optional[dict] = None, timeout: float = 60.0):
    return _http.request_with_retry("GET", url, params=params or {}, timeout=timeout).json()


def _km(lat1, lon1, lat2, lon2) -> float:
    return math.hypot((lon2 - lon1) * 71.5, (lat2 - lat1) * 111.32)


@lru_cache(maxsize=1)
def _station_list() -> tuple:
    """The full CHS station list, fetched once per process (it is ~a thousand rows and
    changes on the timescale of years)."""
    return tuple(_get_json(f"{IWLS}/stations") or [])


def nearest_tide_station(lat: float, lon: float, *, max_km: float = 15.0) -> Optional[TideStation]:
    """The nearest CHS station with predicted water levels (``wlp``) within ``max_km`` of
    the point, or None — inland AOIs simply have no station in reach."""
    stations = _station_list()
    best, best_km = None, None
    for s in stations:
        codes = {t.get("code") for t in (s.get("timeSeries") or [])}
        if "wlp" not in codes:
            continue
        d = _km(lat, lon, s.get("latitude"), s.get("longitude"))
        if best_km is None or d < best_km:
            best, best_km = s, d
    if best is None or best_km > max_km:
        return None
    return TideStation(id=str(best["id"]), name=str(best.get("officialName") or best["id"]),
                       lat=float(best["latitude"]), lon=float(best["longitude"]))


def fetch_tide_predictions(station: TideStation, start: date, end: date) -> TideSeries:
    """Hourly predicted water levels for [start, end] (inclusive), converted to the model
    reference frame (ADR 0024 §1): values shifted from Chart Datum to a geodetic datum
    via the station's published offset, timestamps shifted from UTC to the station's
    local STANDARD time (the clock ECCC rain uses). Chunked to respect the API's
    7-day-per-request cap. Raises when the station lacks a datum conversion or returns
    no data — a tide boundary is never fabricated and never left in the wrong frame."""
    datum = station_datum_offset(station.id)
    if datum is None:
        raise RuntimeError(
            f"CHS station {station.id} publishes no CGVD datum offset; tide boundary "
            "stays off rather than comparing Chart Datum to geodetic inverts")
    datum_code, datum_off = datum
    clock_h = lst_offset_hours(station.lon, station.lat)

    # fetch a UTC day either side so the local-time window stays fully covered
    timestamps: List[datetime] = []
    levels: List[float] = []
    day = start - timedelta(days=1)
    stop = end + timedelta(days=2)
    while day < stop:
        chunk_end = min(day + timedelta(days=_CHUNK_DAYS), stop)
        rows = _get_json(
            f"{IWLS}/stations/{station.id}/data",
            {"time-series-code": "wlp",
             "from": f"{day.isoformat()}T00:00:00Z",
             "to": f"{chunk_end.isoformat()}T00:00:00Z",
             "resolution": _RESOLUTION}) or []
        for r in rows:
            t_utc = datetime.fromisoformat(str(r["eventDate"]).replace("Z", "+00:00")).replace(tzinfo=None)
            t = t_utc + timedelta(hours=clock_h)          # UTC -> local standard time
            if timestamps and t <= timestamps[-1]:
                continue                     # chunk boundaries overlap by one point
            timestamps.append(t)
            levels.append(round(float(r["value"]) + datum_off, 3))   # CD -> geodetic
        day = chunk_end
    lo = datetime(start.year, start.month, start.day)
    hi = datetime(end.year, end.month, end.day) + timedelta(days=1)
    keep = [(t, v) for t, v in zip(timestamps, levels) if lo <= t <= hi]
    if not keep:
        raise RuntimeError(f"CHS station {station.id} returned no wlp data for {start}..{end}")
    return TideSeries(timestamps=[t for t, _ in keep], level_m=[v for _, v in keep],
                      station_name=station.name, datum=datum_code,
                      datum_offset_m=datum_off, clock_utc_offset_h=clock_h)


def tidal_outfall_names(outfalls, max_level_m: float, *, margin_m: float = 0.5) -> list:
    """The outfalls a tide boundary physically affects: invert at or below the window's
    maximum predicted level plus a safety margin. An outfall 20 m above sea level in a
    coastal city is NOT tidal — the criterion is hydraulic, not geographic."""
    return [o.name for o in outfalls if o.invert_m <= max_level_m + margin_m]
