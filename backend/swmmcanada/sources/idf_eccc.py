"""ECCC Engineering Climate Dataset: short-duration rainfall IDF tables (issue #56).

Rational-method pipe sizing needs a design rainfall intensity i(tc, T) in mm/h from
real ECCC IDF tables. ECCC publishes per-station IDF .txt files on
https://collaboration.cmc.ec.gc.ca/cmc/climate/Engineer_Climate/IDF/ but never serves
them individually: the current release (v3-40, Dec 2025) is a single ~665 MB zip of
per-province zips, and the newest *directly fetchable* per-province zips are the
archived v3.20 (2021) release. Downloading 50-150 MB province zips at runtime is not
acceptable, so this module extracts one station .txt (~10 KB) from a province zip with
four small HTTP Range requests (~200 KB total): zip EOCD tail -> central directory ->
local file header -> the member's compressed bytes.

Trade-off (documented, verified live 2026-07): we pin the newest per-province chain --
v3.20 for AB/BC/NB/NL/NT/NU/ON/QC/SK/YT, v3.10 for MB, v3.00 for NS/PE (v3.20/v3.10
only re-released updated provinces). That is one release behind v3-40; per-station
differences are a few percent, immaterial for rational-method sizing. When ECCC
publishes per-province zips of a newer release, add its URL template to
``_DATASET_ZIPS`` and regenerate the index.

Bundled station index ``data/idf_eccc_stations.csv`` (662 stations, all 13
provinces/territories) was generated one-time from the same server: station id, name,
lat/lon from sheet 1 of the official v3.20 station log
(``idf_v-3.20_2021_03_26_log_included_...xlsx``), joined with the exact .txt member
paths listed in each province zip's central directory. Supplemental partner networks
(``IDF_Additional_...zip``) are excluded. Regenerate by re-running that join (log
sheet1 by station id x central-directory .txt members per province zip).

Station .txt layout (identical across v3.00-v3.20; Latin-1 encoded):
- header line ``NAME  PROV  ID`` after the first ``===`` divider;
- ``Table 2b`` "Return Period Rainfall Rates (mm/h)": rows = durations 5 min-24 h,
  columns = return periods 2/5/10/25/50/100 yr, with ``+/-`` confidence rows to skip;
- ``Table 3`` "Interpolation Equation R = A*T^B": fitted power-law coefficients per
  return period, with T the duration in HOURS.

Network failures raise IdfUnavailableError; callers decide the fallback (no default
intensity is baked in here).
"""
import csv
import math
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple, Protocol

from swmmcanada.sources import _http

IDF_RETURN_PERIODS = (2, 5, 10, 25, 50, 100)

_ARCHIVE = "https://collaboration.cmc.ec.gc.ca/cmc/climate/Engineer_Climate/IDF/IDF_archive"
_DATASET_ZIPS = {
    "v3.20": _ARCHIVE + "/idf_v-3.20_2021_3_26/IDF_Files_Fichiers/IDF_v-3.20_2021_03_26_{prov}.zip",
    "v3.10": _ARCHIVE + "/idf_v3-10_2020_03_27/IDF_Files_Fichiers/IDF_v3.10_2020_03_27_{prov}.zip",
    "v3.00": _ARCHIVE + "/idf_v3-00_2019_2_27/IDF_Files_Fichiers/IDF_v3.00_2019_02_27_{prov}.zip",
}
_INDEX_CSV = Path(__file__).parent / "data" / "idf_eccc_stations.csv"
# EOCD record is 22 bytes + up to 65535 bytes of zip comment.
_EOCD_TAIL = 66000


class IdfUnavailableError(RuntimeError):
    """The ECCC IDF source could not deliver a usable table (network/zip/parse)."""


class IdfRangeClient(Protocol):
    def get_bytes(self, url: str, start: Optional[int], end: int) -> bytes:
        """Return bytes [start, end] inclusive; start=None means the last `end` bytes."""
        ...


class RequestsRangeClient:
    """Live IdfRangeClient: HTTP Range requests via `requests`."""

    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout

    def get_bytes(self, url: str, start: Optional[int], end: int) -> bytes:
        rng = f"bytes=-{end}" if start is None else f"bytes={start}-{end}"
        return _http.request_with_retry(
            "GET", url, headers={"Range": rng}, timeout=self.timeout).content


@dataclass(frozen=True)
class IdfStation:
    station_id: str
    name: str
    province: str
    lat: float
    lon: float
    url: str  # province zip on the ECCC collaboration server
    zip_member: str  # station .txt path inside that zip


@dataclass(frozen=True)
class IdfTable:
    station_id: str
    intensities_mm_h: Dict[int, Dict[int, float]]  # {return_period: {duration_min: mm/h}}
    coefficients: Dict[int, Tuple[float, float]]  # {return_period: (A, B)}, i = A*(t_h**B)


_INDEX: Optional[Tuple[IdfStation, ...]] = None


def _load_index() -> Tuple[IdfStation, ...]:
    global _INDEX
    if _INDEX is None:
        stations = []
        with open(_INDEX_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                url = _DATASET_ZIPS[row["dataset_version"]].format(prov=row["province"])
                stations.append(
                    IdfStation(
                        station_id=row["station_id"],
                        name=row["name"],
                        province=row["province"],
                        lat=float(row["lat"]),
                        lon=float(row["lon"]),
                        url=url,
                        zip_member=row["zip_member"],
                    )
                )
        _INDEX = tuple(stations)
    return _INDEX


def nearest_idf_station(
    lat: float, lon: float, *, index: Optional[Sequence[IdfStation]] = None
) -> IdfStation:
    """Nearest station by equirectangular distance, from the bundled index by default."""
    stations = _load_index() if index is None else tuple(index)
    if not stations:
        raise ValueError("empty IDF station index")
    coslat = math.cos(math.radians(lat))

    def dist2(s: IdfStation) -> float:
        return (s.lat - lat) ** 2 + ((s.lon - lon) * coslat) ** 2

    return min(stations, key=dist2)


# ---------------------------------------------------------------------------
# Remote-zip member extraction (3 range requests per station txt)


def _extract_member(client: IdfRangeClient, url: str, member: str) -> bytes:
    tail = client.get_bytes(url, None, _EOCD_TAIL)
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise ValueError(f"zip end-of-central-directory not found in {url}")
    cd_size, cd_offset = struct.unpack("<II", tail[i + 12 : i + 20])
    cd = client.get_bytes(url, cd_offset, cd_offset + cd_size - 1)

    entry = None
    p = 0
    while p + 46 <= len(cd) and cd[p : p + 4] == b"PK\x01\x02":
        (method,) = struct.unpack("<H", cd[p + 10 : p + 12])
        (comp_size,) = struct.unpack("<I", cd[p + 20 : p + 24])
        fn_len, extra_len, comment_len = struct.unpack("<HHH", cd[p + 28 : p + 34])
        (header_offset,) = struct.unpack("<I", cd[p + 42 : p + 46])
        name = cd[p + 46 : p + 46 + fn_len].decode("utf-8", "replace")
        if name == member:
            entry = (header_offset, comp_size, method)
            break
        p += 46 + fn_len + extra_len + comment_len
    if entry is None:
        raise ValueError(f"member {member!r} not found in {url}")

    header_offset, comp_size, method = entry
    head = client.get_bytes(url, header_offset, header_offset + 29)
    if head[:4] != b"PK\x03\x04":
        raise ValueError(f"bad local file header for {member!r} in {url}")
    fn_len, extra_len = struct.unpack("<HH", head[26:30])
    start = header_offset + 30 + fn_len + extra_len
    raw = client.get_bytes(url, start, start + comp_size - 1)
    if method == 0:  # stored
        return raw
    if method == 8:  # deflate
        return zlib.decompressobj(-15).decompress(raw)
    raise ValueError(f"unsupported zip compression method {method} for {member!r}")


# ---------------------------------------------------------------------------
# Station .txt parsing

_STATION_LINE = re.compile(r"^\s*(\S.*?)\s{2,}([A-Z]{2})\s+([0-9A-Z]{7})\s*$")
_DURATION_ROW = re.compile(r"^\s*(\d+)\s*(min|h)\b\s+(.*)$")
_FLOAT = re.compile(r"-?\d+(?:\.\d+)?")


def parse_idf_txt(text: str) -> IdfTable:
    """Parse one ECCC IDF station .txt (Table 2b intensities + Table 3 coefficients)."""
    lines = text.splitlines()

    station_id = ""
    for line in lines[:30]:
        m = _STATION_LINE.match(line)
        if m:
            station_id = m.group(3)
            break

    intensities: Dict[int, Dict[int, float]] = {}
    in_2b = False
    for line in lines:
        if line.lstrip().startswith("Table 2b"):
            in_2b = True
            continue
        if in_2b and line.lstrip().startswith("Table"):
            break
        if not in_2b or "+/-" in line:
            continue
        m = _DURATION_ROW.match(line)
        if not m:
            continue
        duration_min = int(m.group(1)) * (1 if m.group(2) == "min" else 60)
        values = [float(v) for v in _FLOAT.findall(m.group(3))]
        if len(values) < len(IDF_RETURN_PERIODS):
            continue
        for rp, value in zip(IDF_RETURN_PERIODS, values):
            if value > 0:  # -99.9 marks missing data
                intensities.setdefault(rp, {})[duration_min] = value

    if not intensities:
        raise IdfUnavailableError("no 'Table 2b' rainfall-rate table found in IDF txt")

    coefficients: Dict[int, Tuple[float, float]] = {}
    a_values = b_values = None
    for line in lines:
        if "Coefficient (A)" in line:
            a_values = [float(v) for v in _FLOAT.findall(line.split(")", 1)[1])]
        elif "Exponent" in line and "(B)" in line:
            b_values = [float(v) for v in _FLOAT.findall(line.split(")", 1)[1])]
    if a_values and b_values and len(a_values) == len(b_values) == len(IDF_RETURN_PERIODS):
        for rp, a, b in zip(IDF_RETURN_PERIODS, a_values, b_values):
            if a > 0:
                coefficients[rp] = (a, b)

    return IdfTable(station_id=station_id, intensities_mm_h=intensities, coefficients=coefficients)


def fetch_idf_table(
    station: IdfStation,
    *,
    cache_dir: Optional[Path] = None,
    client: Optional[IdfRangeClient] = None,
) -> IdfTable:
    """Station txt from cache_dir (keyed by station id) or via 3 HTTP range requests."""
    cache_path = Path(cache_dir) / f"{station.station_id}.txt" if cache_dir else None
    if cache_path is not None and cache_path.exists():
        return parse_idf_txt(cache_path.read_text(encoding="latin-1"))

    if client is None:
        client = RequestsRangeClient()
    try:
        raw = _extract_member(client, station.url, station.zip_member)
    except Exception as e:
        raise IdfUnavailableError(
            f"failed to fetch IDF table for station {station.station_id} from {station.url}: {e}"
        ) from e
    table = parse_idf_txt(raw.decode("latin-1"))

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)
    return table


def design_intensity_mm_h(table: IdfTable, tc_min: float, return_period: int = 5) -> float:
    """Design intensity at time of concentration tc_min (clamped to the tabulated
    duration range). Prefers the fitted power law i = A*(t_h**B); otherwise log-log
    interpolates between tabulated durations."""
    curve = table.intensities_mm_h.get(return_period)
    if not curve:
        raise ValueError(
            f"return period {return_period} not in table (has {sorted(table.intensities_mm_h)})"
        )
    durations = sorted(curve)
    tc = min(max(float(tc_min), durations[0]), durations[-1])

    coeff = table.coefficients.get(return_period)
    if coeff is not None:
        a, b = coeff
        return a * (tc / 60.0) ** b

    if tc in curve:
        return curve[tc]
    hi = next(d for d in durations if d > tc)
    lo = durations[durations.index(hi) - 1]
    frac = (math.log(tc) - math.log(lo)) / (math.log(hi) - math.log(lo))
    return math.exp(math.log(curve[lo]) + frac * (math.log(curve[hi]) - math.log(curve[lo])))
