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


@dataclass(frozen=True)
class TideStation:
    id: str
    name: str
    lat: float
    lon: float


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
    """Hourly predicted water levels for [start, end] (inclusive), chunked to respect the
    API's 7-day-per-request cap. Raises on an empty result — a tide boundary is never
    fabricated."""
    timestamps: List[datetime] = []
    levels: List[float] = []
    day = start
    stop = end + timedelta(days=1)   # SWMM simulates into the end date; cover it fully
    while day < stop:
        chunk_end = min(day + timedelta(days=_CHUNK_DAYS), stop)
        rows = _get_json(
            f"{IWLS}/stations/{station.id}/data",
            {"time-series-code": "wlp",
             "from": f"{day.isoformat()}T00:00:00Z",
             "to": f"{chunk_end.isoformat()}T00:00:00Z",
             "resolution": _RESOLUTION}) or []
        for r in rows:
            t = datetime.fromisoformat(str(r["eventDate"]).replace("Z", "+00:00")).replace(tzinfo=None)
            if timestamps and t <= timestamps[-1]:
                continue                     # chunk boundaries overlap by one point
            timestamps.append(t)
            levels.append(float(r["value"]))
        day = chunk_end
    if not timestamps:
        raise RuntimeError(f"CHS station {station.id} returned no wlp data for {start}..{end}")
    return TideSeries(timestamps=timestamps, level_m=levels, station_name=station.name)


def tidal_outfall_names(outfalls, max_level_m: float, *, margin_m: float = 0.5) -> list:
    """The outfalls a tide boundary physically affects: invert at or below the window's
    maximum predicted level plus a safety margin. An outfall 20 m above sea level in a
    coastal city is NOT tidal — the criterion is hydraulic, not geographic."""
    return [o.name for o in outfalls if o.invert_m <= max_level_m + margin_m]
