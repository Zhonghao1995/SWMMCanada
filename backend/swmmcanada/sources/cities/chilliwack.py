"""City of Chilliwack storm/sanitary open data -> SWMM ``NetworkIn`` (geometry-inferred
topology, tier-2 Ottawa pattern).

Chilliwack (maps.chilliwack.com — the root services directory is EMPTY; everything lives
in the ``External`` folder) serves StormPipe on ``Dynamic_Utility/8`` with ``INVERT``/
``INVERT_DOWN`` per-end inverts (67% city-wide) and no node ids. The point kit rides ONE
``Dynamic_Utility_Feature/5`` StormSymbol layer typed by ``SYM_TYPE``: MANHOLE/CB-family
symbols carry ``RIM`` elevations (0 = missing) -> node max depths, and the
CATCHBASIN/CB/MH/LAWN BASIN family seeds subcatchments. SanitaryPipe (4) mirrors the pipe
schema for the ADR 0011 tracer; culverts stay on their own layer (6). The server is
WAF-fronted and 403s bursty clients — the shared retrying client handles it.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``INVERT``/``INVERT_DOWN`` (pipes) = pipe-end INVERTS, m AMSL (valley floor ~10 m to
    the hillsides ~350 m); 0 = missing.
  * ``RIM`` (storm symbols) = rim -> node max depths, plausibility-banded.
"""
from swmmcanada.sources.cities import base

BASE_URL = "https://maps.chilliwack.com/arcgis/rest/services/External"
PIPES = ("Dynamic_Utility", 8)
SAN_PIPES = ("Dynamic_Utility", 4)
SYMBOLS = ("Dynamic_Utility_Feature", 5)

CHILLIWACK_CRS = "EPSG:32610"  # UTM 10N (metric ops)
_PAGE = 500                    # small pages — the WAF dislikes big bursts

_RIM_TYPES = {"MANHOLE", "CB/MH", "MANHOLE, DUMMY", "SETTLING CHAMBER"}
_SEED_TYPES = {"CATCHBASIN", "CB/MH", "LAWN BASIN"}


ChilliwackClient = base.ArcGISClient


def _fetch(svc_layer, bbox, client, where="1=1") -> list:
    svc, layer = svc_layer
    return base.fetch_paged(client, f"{BASE_URL}/{svc}/MapServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_chilliwack_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or ChilliwackClient()
    return {
        "mains": _fetch(PIPES, bbox, client),
        "symbols": _fetch(SYMBOLS, bbox, client),
    }


def fetch_chilliwack_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary pipes — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or ChilliwackClient()
    return {"mains": _fetch(SAN_PIPES, bbox, client)}


def fetch_chilliwack_land(bbox, *, client=None) -> dict:
    """Catch-basin-family symbols as seeds; no parcel polygons or building footprints
    are served queryably (the catalogue downloads sit behind an agree-gate)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or ChilliwackClient()
    symbols = _fetch(SYMBOLS, bbox, client)
    seeds = [f for f in symbols
             if str((f.get("properties") or {}).get("SYM_TYPE") or "").strip().upper()
             in _SEED_TYPES]
    return {"catchbasins": seeds, "parcels": [], "buildings": []}


# Plausible elevation band (m AMSL): Fraser valley floor ~8 m to the hillside benches.
_ELEV_MIN, _ELEV_MAX = 0.5, 400.0


def _elev(v):
    """Invert/rim -> float m AMSL inside the band, or None (0 = missing)."""
    f = base.num(v, zero_missing=True)
    return f if (f is not None and _ELEV_MIN <= f <= _ELEV_MAX) else None


_line_ends = base.line_ends

_CHILLIWACK_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_chilliwack_network(data, *, config: base.AssembleConfig = _CHILLIWACK_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes (no node ids); manhole-family symbol RIMs feed max depths."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    symbols = _features((data or {}).get("symbols", []) if isinstance(data, dict) else [])

    pipes = []
    n_no_geom = 0
    seen_names: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        name = str(p.get("AssetID") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('OBJECTID')}"
        dia_mm = base.num(p.get("PIPE_DIAMETER"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("INVERT")), inv_b=_elev(p.get("INVERT_DOWN")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("MATERIAL"), config.default_roughness),
            length_m=base.num(p.get("PIPE_LENGTH"), zero_missing=True),
            shape=p.get("PIPE_SHAPE"),
        ))

    ground_points = []
    for f in symbols:
        p = f.get("properties") or {}
        if str(p.get("SYM_TYPE") or "").strip().upper() not in _RIM_TYPES:
            continue
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _elev(p.get("RIM"))
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    result = base.assemble_network(pipes, ground_points=ground_points, config=config)
    diag = {**result.diagnostics, "city": "chilliwack", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points)}
    return base.NetworkResult(network=result.network, diagnostics=diag)
