"""acquire.climate (spec 02): AOI + date range → tidy per-station ECCC daily rainfall +
temperature, via the MSC GeoMet OGC API (climate-stations + climate-daily). No scraping.

HTTP is behind an injected `ClimateHttpClient` (one `get_json` method) so the whole module
is offline-testable against recorded GeoJSON fixtures. Rainfall resolution is TIERED
(ADR 0014): the nearest station whose hourly PRECIP_AMOUNT covers >=90% of the period is
preferred automatically; otherwise the daily path (v1 behaviour) stands. Temperature and
evaporation stay daily — their consumers (snowmelt min/max, Hargreaves) are daily methods.
"""
import math
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Protocol, Tuple

import pandas as pd
from shapely.geometry import Point

from swmmcanada.build.models import EvaporationSeries, RainfallSeries, TemperatureSeries

BASE = "https://api.weather.gc.ca"

_DAILY_COLS = ["timestamp_local", "precip_mm", "tmin_c", "tmax_c", "tmean_c", "precip_flag", "climate_id"]
_HOURLY_COLS = ["timestamp_local", "precip_mm", "precip_flag", "climate_id"]

# ADR 0014 quality gates: a station's hourly rain is usable only if PRECIP_AMOUNT is
# non-missing for >=90% of the period's hours; an hourly-vs-daily total mismatch beyond
# 15% is surfaced as a validation Warning (the two tiers may come from different stations,
# so this is a sanity signal, not an exact conservation test).
MIN_HOURLY_COVERAGE = 0.90
MISMATCH_WARN_PCT = 15.0


class ClimateHttpClient(Protocol):
    def get_json(self, url: str, params: dict) -> dict:
        ...


@dataclass(frozen=True)
class ClimateStation:
    climate_id: str
    name: Optional[str]
    lon: float
    lat: float
    province: Optional[str] = None
    stn_id: Optional[int] = None


@dataclass(frozen=True)
class ClimateSeries:
    station: ClimateStation
    frame: pd.DataFrame
    source: str = "geomet"


@dataclass(frozen=True)
class ClimateResult:
    stations: List[ClimateStation]
    series: List[ClimateSeries]
    requested_bbox: Tuple[float, float, float, float]
    requested_range: Tuple[date, date]
    warnings: List[str] = field(default_factory=list)
    # ADR 0014: hourly rainfall tier — the series the raingage should use when present,
    # and the honest record of which resolution/station/coverage the build got and why.
    hourly_rain: Optional[ClimateSeries] = None
    forcing: dict = field(default_factory=dict)


def fetch_climate(
    aoi, start: date, end: date, *, client: ClimateHttpClient,
    near_buffer_deg: float = 0.0, max_buffer_tries: int = 40,
) -> ClimateResult:
    """aoi: a geo.AOI (EPSG:4326 geometry + bbox). Selects climate stations inside the AOI
    polygon and pulls each one's daily series for [start, end]. If none are inside and
    `near_buffer_deg > 0`, the station query bbox is expanded and the nearest station to the
    AOI centroid is used (climate stations are sparse)."""
    warnings: List[str] = []
    qbbox = aoi.bbox
    if near_buffer_deg:
        qbbox = (
            qbbox[0] - near_buffer_deg, qbbox[1] - near_buffer_deg,
            qbbox[2] + near_buffer_deg, qbbox[3] + near_buffer_deg,
        )
    stations_fc = client.get_json(
        f"{BASE}/collections/climate-stations/items",
        {"bbox": _bbox_str(qbbox), "f": "json", "limit": 500},
    )
    stations = _parse_stations(stations_fc)
    inside = [s for s in stations if aoi.geometry.contains(Point(s.lon, s.lat))]

    series: List[ClimateSeries] = []
    chosen: List[ClimateStation] = []
    # 1) Stations inside the AOI that actually have data for the period.
    for s in inside:
        frame = _fetch_daily(client, s.climate_id, start, end)
        if _has_precip(frame):
            series.append(ClimateSeries(station=s, frame=frame))
            chosen.append(s)
    # 2) If none, fall back to the nearest buffered station that has data
    #    (the closest stations are often discontinued / precip-only with gaps).
    if not chosen and stations:
        c = aoi.geometry.centroid
        ordered = sorted(stations, key=lambda s: (s.lon - c.x) ** 2 + (s.lat - c.y) ** 2)
        for s in ordered[:max_buffer_tries]:
            frame = _fetch_daily(client, s.climate_id, start, end)
            if _has_precip(frame):
                series.append(ClimateSeries(station=s, frame=frame))
                chosen = [s]
                warnings.append(f"No in-AOI station with data; used nearest with data ({s.climate_id}).")
                break
    if not chosen:
        warnings.append("No climate station with data for the AOI/period.")

    # Hourly rainfall tier (ADR 0014): prefer the nearest station whose hourly
    # PRECIP_AMOUNT actually covers the period; fall back to the daily series otherwise.
    c = aoi.geometry.centroid
    candidates = inside + [s_ for s_ in sorted(
        stations, key=lambda s_: (s_.lon - c.x) ** 2 + (s_.lat - c.y) ** 2) if s_ not in inside]
    hourly_rain, forcing = _hourly_tier(
        client, candidates[:max_buffer_tries], start, end,
        daily_series=(series[0] if series else None))
    if forcing.get("mismatch_warning"):
        warnings.append(forcing["mismatch_warning"])

    return ClimateResult(
        stations=chosen,
        series=series,
        requested_bbox=tuple(aoi.bbox),  # type: ignore[arg-type]
        requested_range=(start, end),
        warnings=warnings,
        hourly_rain=hourly_rain,
        forcing=forcing,
    )


def _hourly_tier(client, candidates, start: date, end: date, *, daily_series):
    """Pick the first candidate station whose hourly rain is usable (ADR 0014) and build the
    honest forcing record. Returns (hourly ClimateSeries | None, forcing dict)."""
    expected_h = ((end - start).days + 1) * 24
    tried = 0
    for s in candidates:
        frame = _fetch_hourly(client, s.climate_id, start, end)
        tried += 1
        n_valid = int(frame["precip_mm"].notna().sum()) if not frame.empty else 0
        coverage = n_valid / expected_h if expected_h else 0.0
        if coverage >= MIN_HOURLY_COVERAGE:
            n_missing = expected_h - n_valid
            forcing = {
                "rainfall_resolution": "hourly",
                "station": s.climate_id, "station_name": s.name,
                "coverage_pct": round(100.0 * coverage, 1),
                "n_missing_hours": n_missing,
                "hourly_total_mm": round(float(frame["precip_mm"].sum(skipna=True)), 2),
            }
            if daily_series is not None and not daily_series.frame.empty:
                daily_total = float(daily_series.frame["precip_mm"].sum(skipna=True))
                forcing["daily_total_mm"] = round(daily_total, 2)
                h = forcing["hourly_total_mm"]
                mismatch = (abs(h - daily_total) / daily_total * 100.0) if daily_total > 0 else (
                    0.0 if h == 0 else 100.0)
                forcing["mismatch_pct"] = round(mismatch, 1)
                if mismatch > MISMATCH_WARN_PCT:
                    forcing["mismatch_warning"] = (
                        f"hourly rain total ({h} mm, {s.climate_id}) differs from the daily "
                        f"station total ({forcing['daily_total_mm']} mm) by {forcing['mismatch_pct']}% "
                        f"— review the raingage source.")
            return ClimateSeries(station=s, frame=frame), forcing
    return None, {
        "rainfall_resolution": "daily",
        "fallback_reason": (
            f"no station within reach has hourly PRECIP_AMOUNT covering "
            f">={int(MIN_HOURLY_COVERAGE * 100)}% of the period ({tried} candidates tried)"),
    }


def _fetch_hourly(client: ClimateHttpClient, climate_id: str, start: date, end: date) -> pd.DataFrame:
    fc = client.get_json(
        f"{BASE}/collections/climate-hourly/items",
        {
            "CLIMATE_IDENTIFIER": climate_id,
            "datetime": f"{start.isoformat()}/{end.isoformat()}",
            "sortby": "LOCAL_DATE",
            "limit": 10000,
            "f": "json",
        },
    )
    return parse_hourly(fc)


def parse_hourly(feature_collection: dict) -> pd.DataFrame:
    """GeoMet climate-hourly FeatureCollection → tidy hourly frame. Trace ('T') precip → 0;
    missing → NaN (counted against the ADR 0014 coverage gate)."""
    rows = []
    for feat in feature_collection.get("features", []):
        p = feat.get("properties", {})
        precip = _f(p.get("PRECIP_AMOUNT"))
        pflag = p.get("PRECIP_AMOUNT_FLAG")
        if math.isnan(precip) and pflag == "T":
            precip = 0.0
        rows.append(
            {
                "timestamp_local": pd.to_datetime(p.get("LOCAL_DATE")),
                "precip_mm": precip,
                "precip_flag": pflag,
                "climate_id": p.get("CLIMATE_IDENTIFIER"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=_HOURLY_COLS)
    return pd.DataFrame(rows, columns=_HOURLY_COLS).sort_values("timestamp_local").reset_index(drop=True)


def _fetch_daily(client: ClimateHttpClient, climate_id: str, start: date, end: date) -> pd.DataFrame:
    fc = client.get_json(
        f"{BASE}/collections/climate-daily/items",
        {
            "CLIMATE_IDENTIFIER": climate_id,
            "datetime": f"{start.isoformat()}/{end.isoformat()}",
            "sortby": "LOCAL_DATE",
            "limit": 10000,
            "f": "json",
        },
    )
    return parse_daily(fc)


def parse_daily(feature_collection: dict) -> pd.DataFrame:
    """GeoMet climate-daily FeatureCollection → tidy daily frame. Trace ('T') precip → 0;
    missing → NaN."""
    rows = []
    for feat in feature_collection.get("features", []):
        p = feat.get("properties", {})
        precip = _f(p.get("TOTAL_PRECIPITATION"))
        pflag = p.get("TOTAL_PRECIPITATION_FLAG")
        if math.isnan(precip) and pflag == "T":
            precip = 0.0
        rows.append(
            {
                "timestamp_local": pd.to_datetime(p.get("LOCAL_DATE")),
                "precip_mm": precip,
                "tmin_c": _f(p.get("MIN_TEMPERATURE")),
                "tmax_c": _f(p.get("MAX_TEMPERATURE")),
                "tmean_c": _f(p.get("MEAN_TEMPERATURE")),
                "precip_flag": pflag,
                "climate_id": p.get("CLIMATE_IDENTIFIER"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=_DAILY_COLS)
    return pd.DataFrame(rows, columns=_DAILY_COLS).sort_values("timestamp_local").reset_index(drop=True)


def to_rainfall_series(series: ClimateSeries, *, gage_name: str = "RG1", ts_name: str = "rain") -> RainfallSeries:
    """Adapt a daily ClimateSeries into build's RainfallSeries (the raingage forcing)."""
    df = series.frame
    timestamps = [t.to_pydatetime() for t in df["timestamp_local"]]
    return RainfallSeries(
        timestamps=timestamps,
        precip_mm=[_precip_or_zero(v) for v in df["precip_mm"]],
        gage_name=gage_name,
        ts_name=ts_name,
    )


def to_temperature_series(series: ClimateSeries) -> Optional[TemperatureSeries]:
    """Daily mean air temperature from the station frame (the forcing-record temperature).

    Missing tmean falls back to (tmin+tmax)/2. Days with no usable temperature are dropped;
    returns None if the station has no usable temperature at all."""
    df = series.frame
    times: List = []
    tmean: List[float] = []
    for t, tmn, tmx, tme in zip(df["timestamp_local"], df["tmin_c"], df["tmax_c"], df["tmean_c"]):
        m = _tmean_or_midpoint(_f(tmn), _f(tmx), _f(tme))
        if m is None:
            continue
        times.append(t.to_pydatetime())
        tmean.append(m)
    if not times:
        return None
    return TemperatureSeries(timestamps=times, tmean_c=tmean)


def to_evaporation_series(
    series: ClimateSeries, *, lat: Optional[float] = None, ts_name: str = "evap"
) -> Optional[EvaporationSeries]:
    """Derive a daily potential-evaporation series (Hargreaves) from the station's
    tmin/tmax/tmean, for the SWMM `[EVAPORATION]` forcing (CONTEXT glossary).

    `lat` defaults to the station latitude (the temperatures' own location). Days missing
    tmin or tmax are skipped (Hargreaves needs the diurnal range); returns None if no day is
    usable or no latitude is available — the caller then omits `[EVAPORATION]` (evap = 0)."""
    if lat is None:
        lat = series.station.lat if series.station is not None else float("nan")
    if math.isnan(lat):
        return None

    df = series.frame
    times: List = []
    evap: List[float] = []
    for t, tmn, tmx, tme in zip(df["timestamp_local"], df["tmin_c"], df["tmax_c"], df["tmean_c"]):
        tmin, tmax = _f(tmn), _f(tmx)
        if math.isnan(tmin) or math.isnan(tmax):
            continue  # Hargreaves needs the diurnal range; SWMM holds the prior day's rate
        tmean = _tmean_or_midpoint(tmin, tmax, _f(tme))
        ts = t.to_pydatetime()
        times.append(ts)
        evap.append(hargreaves_pet(tmin, tmax, tmean, lat, ts.timetuple().tm_yday))
    if not times:
        return None
    return EvaporationSeries(timestamps=times, evap_mm_day=evap, ts_name=ts_name)


# --- Hargreaves potential evaporation (FAO-56) -------------------------------

_SOLAR_CONSTANT = 0.0820  # Gsc, MJ m-2 min-1
_MJ_TO_MM = 0.408         # 1 MJ m-2 day-1 of radiation ≈ 0.408 mm/day of evaporation


def extraterrestrial_radiation(lat_deg: float, doy: int) -> float:
    """Daily extraterrestrial radiation Ra (MJ m-2 day-1), FAO-56 eq. 21. `doy` = day of year."""
    phi = math.radians(lat_deg)
    dr = 1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0)            # inverse Earth–Sun distance
    decl = 0.409 * math.sin(2.0 * math.pi * doy / 365.0 - 1.39)         # solar declination
    # Sunset hour angle; clamp the argument for polar day/night (|tan φ · tan δ| > 1).
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(decl))))
    return (24.0 * 60.0 / math.pi) * _SOLAR_CONSTANT * dr * (
        ws * math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.sin(ws)
    )


def hargreaves_pet(tmin: float, tmax: float, tmean: float, lat_deg: float, doy: int) -> float:
    """Hargreaves potential evaporation (mm/day): 0.0023·Ra·(Tmean+17.8)·√(Tmax−Tmin),
    with Ra expressed in mm/day. Clamped to ≥ 0 (a sub-freezing Tmean would otherwise go
    negative); the diurnal range is clamped to ≥ 0 against bad tmax<tmin records."""
    ra_mm = _MJ_TO_MM * extraterrestrial_radiation(lat_deg, doy)
    pet = 0.0023 * ra_mm * (tmean + 17.8) * math.sqrt(max(0.0, tmax - tmin))
    return max(0.0, pet)


def _tmean_or_midpoint(tmin: float, tmax: float, tmean: float) -> Optional[float]:
    """Mean temperature, falling back to the tmin/tmax midpoint; None if unavailable."""
    if not math.isnan(tmean):
        return tmean
    if not math.isnan(tmin) and not math.isnan(tmax):
        return (tmin + tmax) / 2.0
    return None


# --- internals ---------------------------------------------------------------


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _has_precip(frame) -> bool:
    """A station is usable for rainfall only if its frame has >=1 non-missing precip value.
    A non-empty frame can still be precip-less (e.g. a temperature-only / gappy station),
    which would otherwise feed an all-NaN raingage and NaN the whole SWMM run."""
    return not frame.empty and bool(frame["precip_mm"].notna().any())


def _precip_or_zero(v) -> float:
    """Missing daily precip (NaN) -> 0 mm, so a residual gap can't make the model NaN."""
    fv = _f(v)
    return 0.0 if math.isnan(fv) else fv


def _bbox_str(bbox) -> str:
    return ",".join(str(round(v, 6)) for v in bbox)


def _parse_stations(fc: dict) -> List[ClimateStation]:
    out: List[ClimateStation] = []
    for feat in fc.get("features", []):
        p = feat.get("properties", {})
        coords = (feat.get("geometry") or {}).get("coordinates") or [None, None]
        out.append(
            ClimateStation(
                climate_id=p.get("CLIMATE_IDENTIFIER"),
                name=p.get("STATION_NAME"),
                lon=_f(coords[0]),
                lat=_f(coords[1]),
                province=p.get("PROV_STATE_TERR_CODE"),
                stn_id=p.get("STN_ID"),
            )
        )
    return out
