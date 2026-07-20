"""Reykjavík / capital-area fráveita open data -> SWMM ``NetworkIn`` (geometry-inferred topology).

The Icelandic municipal drainage register (Veitur / Orkuveita Reykjavíkur's LÚKOR, and the
neighbouring municipalities' LÚKR feeds) follows the national *fitjuskrá* feature standard, so
every city here shares ONE schema:

  * Pipes (``Fráveitulagnir``, polyline) carry EFNISGERD (material), TVERMAL (diameter, mm),
    INNIHALD (contents: regnvatn=storm / blandað=combined / skólp=sanitary), RENNSLI (flow) and
    Shape__Length — but **no inverts and no node ids**, so connectivity is inferred from polyline
    endpoints by ``cities.base`` (coordinate snapping), exactly like Ottawa.
  * Structures (``Fráveitubúnaður``, point) carry the elevations the pipes lack: HAED (rim/ground)
    and **BOTNKODI / Rennslishæð (invert / flow elevation)**, plus HLUTUR (type: Brunnur=manhole,
    Endi=outfall, Niðurfall=inlet…) and AUDKENNI (asset id).

So Reykjavík is Ottawa's topology (endpoint-snapped) but with REAL surveyed inverts — attached to
the structure points, not the pipe ends. ``build_reykjavik_network`` therefore snaps each pipe
endpoint to its nearest structure and lifts that structure's BOTNKODI onto the pipe end (``inv_a``/
``inv_b``), its HAED into ``ground_points``, its AUDKENNI into ``label_points`` (so nodes keep real
manhole ids) and Endi/útrás structures into ``outfall_points``. The shared assembler does the rest.

Combined mains (INNIHALD=blandað) join the storm system (ADR 0021, as in Ottawa/Vancouver); the
separated sanitary lines are the second tagged system (ADR 0011). All source geometry is ISN93
(EPSG:3057) and requested back in EPSG:4326 by the shared fetch loop. See fixtures/reykjavik/README.md.
"""
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from swmmcanada.sources.cities import base

Coord = Tuple[float, float]

# === DATA SOURCE — the ONE thing to swap when the Reykjavík host is confirmed ===============
# Reykjavík target (Veitur / Orkuveita Reykjavíkur's own register). Geo-restricted to `.is`
# today (ECONNREFUSED from outside Iceland), so it is not yet the default:
#     LUKOR = "https://lukor.or.is/arcgis/rest/services/lukor/lukor_overlay/MapServer"
#   an ArcGIS MapServer — point PIPES_URL/STRUCT_URL/INLETS_URL at its fráveitulagnir /
#   fráveitubúnaður / niðurföll layers; no other change (both hosts serve Esri JSON — see _fetch).
# Currently wired to the shared-*fitjuskrá*-schema Kópavogur (LÚKK) open service — token-free,
# VERIFIED 2026-07-17. Identical field names, so the adapter logic is unchanged; only these URLs move.
# For Reykjavík, the land layers live on the City's LÚKR open portal (lukrgatt.reykjavik.is:
# OpinGognLodirLodamork / OpinGognHus / OpinGognSamgongur-inlets), NOT on the LÚKOR drainage host.
_ORG = "https://services7.arcgis.com/ZIyvqwbcRPyMF4iE/arcgis/rest/services"
PIPES_URL = f"{_ORG}/Fráveitulagnir/FeatureServer/9"       # sewer mains (polyline)
STRUCT_URL = f"{_ORG}/Fráveitubúnaður/FeatureServer/4"     # manholes/structures (point): HAED + BOTNKODI
INLETS_URL = f"{_ORG}/Niðurföll/FeatureServer/0"           # catch basins (Niðurföll): subcatchment seeds
PARCELS_URL = f"{_ORG}/Landeignir/FeatureServer/2"         # land parcels (Landeignir): lot-line subcatchments
BUILDINGS_URL = f"{_ORG}/Hús/FeatureServer/0"              # building footprints (Hús): imperviousness

REYKJAVIK_CRS = "EPSG:3057"  # ISN93 (metric ops: subcatchments, snapping tolerances)
_PAGE = 2000  # the hosted FeatureServer's maxRecordCount — one page per 2000 features

# INNIHALD (contents) vocabulary -> tagged system. Combined joins storm (ADR 0021). Unknown/blank
# stays with storm (a fráveita line with no content tag is far more likely drainage than the
# treatment-bound sanitary trunk) and is counted in diagnostics rather than silently dropped.
_STORM = {"ofanvatn", "regnvatn", "regn", "regnvatns", "ofanvatns"}
_COMBINED = {"blandað", "blandad", "blönduð", "blonduð"}
_SANITARY = {"skólp", "skolp", "klóak", "kloak", "saur", "frárennsli", "frarennsli"}

# HLUTUR (structure type) TOKENS that mark a drainage OUTFALL (network exit to a receiving water).
# Matched per-token, not whole-string, because the real field carries free-text like
# "Nidurflogn 6 Endi" alongside the clean "endi" (audit 2026-07-17 over all 29k Kópavogur structures).
_OUTFALL_TOKENS = {"endi", "útrás", "utras", "losun", "útrend", "utrend"}

# EFNISGERD (material) -> Manning's n. Icelandic material words; falls through to base's uppercase
# code table (PVC/CONC/…) and then the 0.013 default for anything unrecognised.
_IS_MATERIAL: Dict[str, float] = {
    "steypa": 0.013, "steinsteypa": 0.013,          # concrete
    "plast": 0.010, "pvc": 0.010, "pe": 0.011, "peh": 0.011, "hdpe": 0.011,   # plastics
    "leir": 0.013, "leirrör": 0.013,                # vitrified clay
    "steinn": 0.015, "múrsteinn": 0.015, "hlaðið": 0.015,   # stone / brick
    "asbest": 0.011, "asbestsement": 0.011,         # asbestos cement
    "járn": 0.013, "steypujárn": 0.013,             # cast iron
    "stál": 0.012, "stal": 0.012,                   # steel (audit 2026-07-17)
    # "óþekkt" (unknown) / None -> default, handled by the fall-through.
}

# Structure-invert snap tolerance: a surveyed manhole point sits within a couple of metres of the
# pipe vertex that meets it; beyond ~5 m the "nearest structure" is not this pipe's node.
_SNAP_TOL_M = 5.0
_CELL_DEG = 0.0006  # coarse lookup grid, comfortably larger than _SNAP_TOL_M at ~64°N (3x3 covers it)

# Endpoints snap to one node within ~1 m (as Ottawa): the mains do not always share an exact vertex.
_REYKJAVIK_ASSEMBLE = base.AssembleConfig(snap_decimals=5)

ReykjavikClient = base.ArcGISClient


# --- structures: the invert/rim/outfall carrier -------------------------------------------
@dataclass(frozen=True)
class _Struct:
    coord: Coord                 # (lon, lat), EPSG:4326
    invert: Optional[float]      # BOTNKODI (Rennslishæð) — flow-line elevation (m)
    rim: Optional[float]         # HAED — ground/rim elevation (m)
    hlutur: str                  # structure type (Brunnur/Endi/Niðurfall…)
    node_id: Optional[str]       # AUDKENNI — preferred SWMM node id


def _struct_id(props: dict) -> Optional[str]:
    for key in ("AUDKENNI", "nyttAudkenni", "NUMER", "OBJECTID"):
        v = props.get(key)
        if v not in (None, ""):
            return str(v)
    return None


def _cell(xy: Coord) -> Tuple[int, int]:
    return (int(xy[0] // _CELL_DEG), int(xy[1] // _CELL_DEG))


def _struct_index(structures: list) -> Dict[Tuple[int, int], List[_Struct]]:
    """Bucket structure points into a coarse grid so each pipe endpoint finds its node in O(1)."""
    idx: Dict[Tuple[int, int], List[_Struct]] = defaultdict(list)
    for f in structures:
        c = (f.get("geometry") or {}).get("coordinates")
        if not c or len(c) < 2:
            continue
        p = f.get("properties") or {}
        rec = _Struct(coord=(c[0], c[1]), invert=base.num(p.get("BOTNKODI")),
                      rim=base.num(p.get("HAED")), hlutur=str(p.get("HLUTUR") or "").strip(),
                      node_id=_struct_id(p))
        idx[_cell(rec.coord)].append(rec)
    return idx


def _nearest_struct(xy: Coord, idx) -> Optional[_Struct]:
    """The structure closest to ``xy`` within ``_SNAP_TOL_M`` (searching the 3x3 cell block), else None."""
    cx, cy = _cell(xy)
    best, best_d = None, None
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for rec in idx.get((cx + dx, cy + dy), ()):
                d = base._haversine_m(xy, rec.coord)
                if d <= _SNAP_TOL_M and (best_d is None or d < best_d):
                    best, best_d = rec, d
    return best


def _is_outfall(hlutur: str) -> bool:
    """True when any whitespace/punctuation token of the (messy, free-text) HLUTUR value is an
    outfall keyword — catches both the clean "endi" and free-text "Nidurflogn 6 Endi"."""
    return any(t in _OUTFALL_TOKENS for t in re.split(r"[\s/,;_-]+", hlutur.strip().lower()))


# SWMM folds every object name to ONE case (node "NF10" == "nf10"), so all id handling below keys
# on the CASE-FOLDED id. The shared assembler names unlabelled nodes ``N1, N2, …`` (no leading
# zero), and a real AUDKENNI of that exact shape would collide into one SWMM node — Kópavogur really
# publishes a mixed ``N10/BR14/NF10`` manhole vocabulary — so that namespace is reserved.
_RESERVED_ID = re.compile(r"^n[1-9]\d*$")   # matched against the lower-cased id


_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(raw: str) -> str:
    """A valid SWMM object name: drop everything outside ``[A-Za-z0-9._-]``. Real AUDKENNI carry
    spaces and quotes ("Sk logn steinn 150", "Nf2 ", a stray "), which SWMM's whitespace/quote-
    delimited name syntax cannot hold — they truncate and collide. Returns "" if nothing survives."""
    return _UNSAFE_NAME_CHARS.sub("", (raw or "").strip())


def _safe_labels(label_points, snap_decimals: int) -> Tuple[list, int, int]:
    """Keep an AUDKENNI as a node id only when it is a valid, unambiguous SWMM name. Real-LÚKK
    hazards, all handled here: (0) ids with spaces/quotes/other chars SWMM can't hold are
    SANITISED first (``_safe_name``); then, keyed on the CASE-FOLDED sanitised id (SWMM folds
    names): (1) ids not globally unique across a fetch window (R./S./N./BR series restart) resolve
    to >1 node; (2) mixed-case variants (``nf10`` vs ``NF10``) SWMM treats as one; (3) ids matching
    the assembler's generated ``N#`` namespace. All are dropped (node gets a generated id); a clean
    unique id keeps its sanitised form. Returns ``(kept, n_dropped_nonunique, n_dropped_reserved)``."""
    cleaned = [(xy, _safe_name(lab)) for xy, lab in label_points]
    coords_by_key: Dict[str, set] = defaultdict(set)
    for xy, lab in cleaned:
        if lab:
            coords_by_key[lab.lower()].add((round(xy[0], snap_decimals), round(xy[1], snap_decimals)))
    kept, n_dup, n_reserved = [], 0, 0
    for xy, lab in cleaned:
        if not lab:
            n_dup += 1            # nothing survived sanitising -> fall back to a generated id
            continue
        key = lab.lower()
        if _RESERVED_ID.match(key):
            n_reserved += 1
        elif len(coords_by_key[key]) != 1:
            n_dup += 1
        else:
            kept.append((xy, lab))
    return kept, n_dup, n_reserved


def _roughness(efnisgerd: Optional[str], default: float = 0.013) -> float:
    if efnisgerd is None:
        return default
    key = str(efnisgerd).strip().lower()
    if key in _IS_MATERIAL:
        return _IS_MATERIAL[key]
    return base.material_roughness(efnisgerd, default)   # try the shared uppercase code table


def _system_of(feature: dict) -> str:
    """Tag a pipe by INNIHALD: 'storm' | 'combined' | 'sanitary' (unknown/blank -> 'storm')."""
    val = str((feature.get("properties") or {}).get("INNIHALD") or "").strip().lower()
    if val in _SANITARY:
        return "sanitary"
    if val in _COMBINED:
        return "combined"
    return "storm"


# --- fetch --------------------------------------------------------------------------------
def _fetch(url, bbox, client, where="1=1") -> list:
    """Paginated bbox query via the shared loop, ALWAYS as Esri JSON + convert. Two reasons:
    (1) Esri JSON reports the paging flag (``exceededTransferLimit``) top-level on every host —
    GeoJSON nests it under ``.properties`` on hosted FeatureServers, the trap that silently
    truncated >1-page GeoJSON fetches (a 6428-pipe AOI returned exactly 1000) until
    ``base.fetch_paged`` learned to read both places; (2) both the Kópavogur hosted FeatureServer
    and the Reykjavík LÚKOR MapServer serve Esri JSON, so one path covers the host swap.
    See fixtures/reykjavik/README.md."""
    return base.fetch_paged(client, f"{url}/query", bbox, where=where, fmt="json",
                            page_size=_PAGE, transform=base.esri_to_geojson)


def _fetch_network(bbox, client) -> Tuple[list, list]:
    """All fráveita mains + all structures intersecting ``bbox`` (structures are shared across the
    storm and sanitary systems, so both fetchers reuse them)."""
    return _fetch(PIPES_URL, bbox, client), _fetch(STRUCT_URL, bbox, client)


def _repair_polygons(features: list) -> list:
    """Repair invalid polygon geometries (``buffer(0)``) and drop the unrepairable/empty. Real LÚKK
    Hús/Landeignir data carries the occasional self-intersecting footprint (~1%), which crashes the
    delineator's ``unary_union`` (side-location conflict) — the shared code does not repair its land
    inputs, so the adapter hands it clean polygons."""
    from shapely.geometry import mapping, shape
    out = []
    for f in features:
        g = f.get("geometry")
        if not g:
            continue
        try:
            geom = shape(g)
            if not geom.is_valid:
                geom = geom.buffer(0)
            if geom.is_empty:
                continue
            out.append({**f, "geometry": mapping(geom)})
        except Exception:  # noqa: BLE001 — a single unparseable footprint must not sink the fetch
            continue
    return out


def fetch_reykjavik_storm(bbox, *, client=None) -> dict:
    """Storm + combined mains (ADR 0021: blandað mains carry the stormwater) with the shared
    structures. Returns ``{"pipes", "structures"}`` (lists of GeoJSON Features)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or ReykjavikClient()
    pipes, structs = _fetch_network(bbox, client)
    return {"pipes": [f for f in pipes if _system_of(f) != "sanitary"], "structures": structs}


def fetch_reykjavik_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary mains only (INNIHALD=skólp/klóak…) — the second tagged system (ADR 0011).
    Same schema + same structures, so :func:`build_reykjavik_network` assembles it unchanged."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or ReykjavikClient()
    pipes, structs = _fetch_network(bbox, client)
    return {"pipes": [f for f in pipes if _system_of(f) == "sanitary"], "structures": structs}


def fetch_reykjavik_land(bbox, *, client=None) -> dict:
    """Catch basins (Niðurföll) + parcels (Landeignir) + building footprints (Hús) for the
    parcel/building subcatchment method (ADR 0005): cells follow real lot lines and imperviousness =
    roofs + road right-of-way. Where parcels are absent the delineator falls back to catch-basin
    Voronoi. Returns ``{"catchbasins", "parcels", "buildings"}`` (lists of GeoJSON Features)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or ReykjavikClient()
    return {"catchbasins": _fetch(INLETS_URL, bbox, client),
            "parcels": _repair_polygons(_fetch(PARCELS_URL, bbox, client)),
            "buildings": _repair_polygons(_fetch(BUILDINGS_URL, bbox, client))}


# --- network assembly ---------------------------------------------------------------------
def build_reykjavik_network(data, *, config: base.AssembleConfig = _REYKJAVIK_ASSEMBLE) -> base.NetworkResult:
    """Snap pipe endpoints to structures (lifting BOTNKODI inverts + HAED rims + AUDKENNI ids +
    Endi outfalls onto the network), then hand canonical pipes to the shared assembler."""
    pipes_f = data["pipes"] if isinstance(data, dict) else list(data)
    structures = data.get("structures", []) if isinstance(data, dict) else []
    sidx = _struct_index(structures)

    pipes: List[base.RawPipe] = []
    ground_points: List[Tuple[Coord, float]] = []
    label_points: List[Tuple[Coord, str]] = []
    outfall_points: List[Coord] = []
    seen: Dict[str, int] = {}
    n_no_geom = n_snapped = n_no_struct = n_bad_invert = 0

    def _snap_end(xy: Coord) -> Optional[float]:
        """Lift a matched structure's elevations/id/outfall-role onto endpoint ``xy``; return its
        invert (BOTNKODI) for the pipe end, or None when no structure is within tolerance."""
        nonlocal n_snapped, n_no_struct, n_bad_invert
        s = _nearest_struct(xy, sidx)
        if s is None:
            n_no_struct += 1
            return None
        n_snapped += 1
        if s.rim is not None:
            ground_points.append((xy, s.rim))
        if s.node_id:
            label_points.append((xy, s.node_id))
        if _is_outfall(s.hlutur):
            outfall_points.append(xy)
        # Reject an invert published ABOVE its own rim (physically impossible — bad LÚKK survey
        # rows exist): treat as missing so the assembler gap-fills it, instead of seating the node
        # bottom above ground. The rim above is still usable.
        if s.invert is not None and s.rim is not None and s.invert > s.rim:
            n_bad_invert += 1
            return None
        return s.invert

    for f in pipes_f:
        p = f.get("properties") or {}
        a, b = base.line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        oid = p.get("OBJECTID")
        name = _safe_name(str(p.get("AUDKENNI") or "")) or f"P{oid}"   # valid SWMM link name
        seen[name.lower()] = seen.get(name.lower(), 0) + 1   # SWMM folds link names case-insensitively
        if seen[name.lower()] > 1:                            # keep conduit names unique under folding
            name = f"{name}_{oid}"
        dia = base.num(p.get("TVERMAL"))                # diameter in mm
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_snap_end(a), inv_b=_snap_end(b),
            diameter_m=(dia / 1000.0) if (dia and dia > 0) else None,
            roughness_n=_roughness(p.get("EFNISGERD"), config.default_roughness),
            length_m=base.num(p.get("Shape__Length")),
        ))

    label_points, n_lab_dup, n_lab_reserved = _safe_labels(label_points, config.snap_decimals)
    result = base.assemble_network(pipes, outfall_points=outfall_points, ground_points=ground_points,
                                   label_points=label_points, config=config)
    diag = {**result.diagnostics, "city": "reykjavik", "n_pipes_in": len(pipes_f),
            "n_structures": len(structures), "n_ends_snapped": n_snapped,
            "n_ends_no_struct": n_no_struct, "n_no_geom": n_no_geom,
            "n_inverts_above_rim_rejected": n_bad_invert,
            "n_struct_outfalls": len(outfall_points),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
