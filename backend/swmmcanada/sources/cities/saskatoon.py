"""City of Saskatoon storm/sanitary sewer data -> SWMM ``NetworkIn`` (geometry topology,
labelled ends).

Saskatoon's network lives on the city's own ArcGIS Server (gisext.saskatoon.ca) in the
``Core/WSSTreatment_AGOL`` MapServer — a public, token-free service backing the city's web
maps, but NOT in the ``OD`` open-data folder (which carries only LandSurface/Transportation;
parcels come from there). That provenance is recorded honestly: network = public city
service, licence unstamped; parcels = official open data.

Storm Main (5) and Sanitary Main (1) share one schema: ``UPELEV``/``DOWNELEV`` per-end
inverts, ``FROMMH``/``TOMH`` node ids joining the manhole layers' ``FACILITYID`` (verified
numerically: a pipe's UPELEV equals its manhole's INVERTELEV), ``DIAMETER`` (mm),
``MAINSHAPE``, ``MATERIAL`` codes (CP/CT/PVC/...). The gravity graph filters
``STATUS='A1'`` (active; D3 = decommissioned) and ``PIPETYPE IN ('Main','Trunk',
'Bypass Main')`` — Catch Basin Leads and Subdrainage Mains stay out. No explicit outfall
layer exists city-wide; the assembler's per-component sinks stand in at the river edges.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UPELEV``/``DOWNELEV`` (mains) = pipe-end INVERTS, m AMSL (~475+ across Saskatoon);
    0 = missing.
  * ``RIMELEV`` (manholes) = rim -> node max depths, plausibility-banded.
  * Manhole ``INVERTELEV``/``INVERT`` (chamber invert/depth) are deliberately NOT read —
    node invert stays the lowest connected pipe-end invert (ASSUMPTIONS.md).
"""
from swmmcanada.sources.cities import base

CORE = "https://gisext.saskatoon.ca/arcgis/rest/services/Core/WSSTreatment_AGOL/MapServer"
OD_LAND = "https://gisext.saskatoon.ca/arcgisod/rest/services/OD/LandSurface/MapServer"
STORM_MAINS, STORM_MANHOLES, STORM_CATCHBASINS = 5, 6, 7
SAN_MAINS, SAN_MANHOLES = 1, 2
PARCELS = 1                     # OD/LandSurface: "City of Saskatoon - Parcel"

SASKATOON_CRS = "EPSG:32613"    # UTM 13N (metric ops) — same zone as Regina
_PAGE = 2000

_MAIN_WHERE = "STATUS='A1' AND PIPETYPE IN ('Main','Trunk','Bypass Main')"
_NODE_WHERE = "STATUS='A1'"


SaskatoonClient = base.ArcGISClient


def _fetch(service, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{service}/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_saskatoon_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SaskatoonClient()
    return {
        "mains": _fetch(CORE, STORM_MAINS, bbox, client, where=_MAIN_WHERE),
        "manholes": _fetch(CORE, STORM_MANHOLES, bbox, client, where=_NODE_WHERE),
    }


def fetch_saskatoon_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary gravity system — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SaskatoonClient()
    return {
        "mains": _fetch(CORE, SAN_MAINS, bbox, client, where=_MAIN_WHERE),
        "manholes": _fetch(CORE, SAN_MANHOLES, bbox, client, where=_NODE_WHERE),
    }


def fetch_saskatoon_land(bbox, *, client=None) -> dict:
    """Active catch basins + official open-data parcels; no public building footprints."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SaskatoonClient()
    return {
        "catchbasins": _fetch(CORE, STORM_CATCHBASINS, bbox, client, where=_NODE_WHERE),
        "parcels": _fetch(OD_LAND, PARCELS, bbox, client),
        "buildings": [],
    }


def _elev(v):
    """Invert/rim -> float m AMSL, or None (0 = missing; Saskatoon sits ~475+ m)."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): South Saskatchewan valley ~450 m to ~560 m uplands.
_RIM_MIN, _RIM_MAX = 400.0, 650.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_line_ends = base.line_ends

_SASKATOON_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_saskatoon_network(data, *, config: base.AssembleConfig = _SASKATOON_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes from mains (storm/sanitary share the schema), FROMMH/TOMH as node
    labels, rims from manholes. MAINSHAPE rides the #130 shape mapping where non-circular
    dims exist (DIAMETER doubles as the circular bore)."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    manholes = _features((data or {}).get("manholes", []) if isinstance(data, dict) else [])

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
                label_points.append((xy, f"MH{tid}"))   # numeric ids get a stable prefix

    ground_points = []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _rim((f.get("properties") or {}).get("RIMELEV"))
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, ground_points=ground_points, label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "saskatoon", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
