"""City of Peterborough storm/sanitary data -> SWMM ``NetworkIn`` (geometry topology,
labelled ends).

Peterborough serves one ``SanStormExternal`` MapServer on its own host
(citymaps.peterborough.ca). The schema is the Saskatoon/Toronto family: ``UPELEV``/
``DOWNELEV`` per-end inverts on Storm Gravity Main (18, ``WATERTYPE='SW'``) and San
Gravity Main (5), ``FROMMH``/``TOMH`` numeric node ids (labelled with an ``MH`` prefix),
``DIAMETER`` mm + ``MAINSHAPE`` + ``MATERIAL`` codes. Storm Manhole (13) carries
``RIMELEV``; Storm Discharge Point (11) is the outfall layer; Storm Inlet (12) seeds
subcatchments. No parcel/building layers on this host — land-cover imperviousness.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UPELEV``/``DOWNELEV`` = pipe-end INVERTS, m AMSL (~180-330 m across Peterborough);
    0 = missing.
  * ``RIMELEV`` (manholes) = rim -> node max depths, plausibility-banded; ``INVERTELEV``/
    ``HIGHELEV`` (chamber invert / high pipe) deliberately not read (ASSUMPTIONS.md).
"""
from swmmcanada.sources.cities import base

ARC = "https://citymaps.peterborough.ca/arcgis/rest/services/SanStormExternal/MapServer"
STORM_MAINS, STORM_MANHOLES, STORM_OUTFALLS, STORM_INLETS = 18, 13, 11, 12
SAN_MAINS, SAN_MANHOLES = 5, 2

PETERBOROUGH_CRS = "EPSG:32617"  # UTM 17N (metric ops)
_PAGE = 1000                     # layer maxRecordCount

_STORM_WHERE = "WATERTYPE='SW'"


PeterboroughClient = base.ArcGISClient


def _fetch(layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ARC}/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_peterborough_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or PeterboroughClient()
    return {
        "mains": _fetch(STORM_MAINS, bbox, client, where=_STORM_WHERE),
        "manholes": _fetch(STORM_MANHOLES, bbox, client),
        "outfalls": _fetch(STORM_OUTFALLS, bbox, client),
    }


def fetch_peterborough_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary gravity system — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or PeterboroughClient()
    return {
        "mains": _fetch(SAN_MAINS, bbox, client),
        "manholes": _fetch(SAN_MANHOLES, bbox, client),
    }


def fetch_peterborough_land(bbox, *, client=None) -> dict:
    """Storm inlets as seeds; no parcel/building layers on this host."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or PeterboroughClient()
    return {
        "catchbasins": _fetch(STORM_INLETS, bbox, client),
        "parcels": [],
        "buildings": [],
    }


def _elev(v):
    """Invert/rim -> float m AMSL, or None (0 = missing; the city sits ~180-330 m)."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): Otonabee valley ~180 m to the drumlin tops ~330 m.
_RIM_MIN, _RIM_MAX = 150.0, 400.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_line_ends = base.line_ends

_PETERBOROUGH_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_peterborough_network(data, *, config: base.AssembleConfig = _PETERBOROUGH_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes from gravity mains (storm/sanitary share the schema), MH-prefixed
    FROMMH/TOMH labels, rims from manholes, outfalls from discharge points."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    manholes = _features((data or {}).get("manholes", []) if isinstance(data, dict) else [])
    outfall_feats = _features((data or {}).get("outfalls", []) if isinstance(data, dict) else [])

    pipes, label_points = [], []
    n_no_geom = 0
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        dia_mm = base.num(p.get("DIAMETER"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=str(p.get("FACILITYID") or p.get("OBJECTID")),
            end_a=a, end_b=b,
            inv_a=_elev(p.get("UPELEV")), inv_b=_elev(p.get("DOWNELEV")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("MATERIAL"), config.default_roughness),
        ))
        for xy, tid in ((a, p.get("FROMMH")), (b, p.get("TOMH"))):
            tid = str(tid or "").strip()
            if tid:
                label_points.append((xy, f"MH{tid}"))

    ground_points = []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _rim((f.get("properties") or {}).get("RIMELEV"))
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    outfall_points = []
    for f in outfall_feats:
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) >= 2:
            outfall_points.append((c[0], c[1]))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, outfall_points=outfall_points, ground_points=ground_points,
        label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "peterborough", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
