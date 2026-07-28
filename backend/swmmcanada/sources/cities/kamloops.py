"""City of Kamloops drainage/sanitary open data -> SWMM ``NetworkIn`` (geometry-inferred
topology — the tier-2 Ottawa pattern with a novel missing-data sentinel).

Kamloops (maps.kamloops.ca ``OpenData`` folder) publishes DGravityMain (12) with
``UPSTREAMINVERT``/``DOWNSTREAMINVERT`` populated on 94% city-wide — but the missing
sentinel is the literal number **9999** (not 0/null): first pages of the table are nearly
all 9999, which almost mislabelled the whole city during scouting. No node ids exist —
topology comes from endpoint snapping. DManhole (14) carries ``RIMELEVATION`` (same 9999
convention), DOutlet (16) is the outfall layer, DCatchBasin (2) seeds subcatchments.
``OpenDataSanitaryTel`` mirrors the schema for the ADR 0011 tracer;
``OpenDataPlanimetric/39`` has building footprints (no parcel polygons in the catalogue).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UPSTREAMINVERT``/``DOWNSTREAMINVERT`` = pipe-end INVERTS, m AMSL (~340-480 across
    the valley); **9999 = missing** (and 0 is treated as missing too, defensively).
  * ``RIMELEVATION`` (manholes) = rim -> node max depths, same sentinel + band.
"""
from swmmcanada.sources.cities import base

ARC = "https://maps.kamloops.ca/arcgis/rest/services/OpenData"
DRAIN, SANITARY, PLANIMETRIC = "OpenDataDrainEmerGeo", "OpenDataSanitaryTel", "OpenDataPlanimetric"
D_MAINS, D_MANHOLES, D_OUTLETS, D_CATCHBASINS = 12, 14, 16, 2
S_MAINS, S_MANHOLES = 12, 13
BUILDINGS = 39

KAMLOOPS_CRS = "EPSG:32610"  # UTM 10N (metric ops)
_PAGE = 1000


KamloopsClient = base.ArcGISClient


def _fetch(svc, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ARC}/{svc}/MapServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_kamloops_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KamloopsClient()
    return {
        "mains": _fetch(DRAIN, D_MAINS, bbox, client),
        "manholes": _fetch(DRAIN, D_MANHOLES, bbox, client),
        "outfalls": _fetch(DRAIN, D_OUTLETS, bbox, client),
    }


def fetch_kamloops_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary gravity system — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KamloopsClient()
    return {
        "mains": _fetch(SANITARY, S_MAINS, bbox, client),
        "manholes": _fetch(SANITARY, S_MANHOLES, bbox, client),
    }


def fetch_kamloops_land(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KamloopsClient()
    return {
        "catchbasins": _fetch(DRAIN, D_CATCHBASINS, bbox, client),
        "parcels": [],
        "buildings": _fetch(PLANIMETRIC, BUILDINGS, bbox, client),
    }


# Kamloops sits ~335 m (river) to ~800 m (Aberdeen); 9999 is the city's missing sentinel.
_ELEV_MIN, _ELEV_MAX = 300.0, 900.0


def _elev(v):
    """Invert/rim -> float m AMSL, or None. The missing sentinel is the literal 9999
    (screened by the plausibility band, which also drops 0 and other junk)."""
    f = base.num(v)
    return f if (f is not None and _ELEV_MIN <= f <= _ELEV_MAX) else None


_line_ends = base.line_ends

_KAMLOOPS_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_kamloops_network(data, *, config: base.AssembleConfig = _KAMLOOPS_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes (no node ids — endpoint snapping); manhole rims; outlet outfalls."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    manholes = _features((data or {}).get("manholes", []) if isinstance(data, dict) else [])
    outfall_feats = _features((data or {}).get("outfalls", []) if isinstance(data, dict) else [])

    pipes = []
    n_no_geom = 0
    seen_names: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        name = str(p.get("FACILITYID") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('OBJECTID')}"
        dia_mm = base.num(p.get("DIAMETER"), zero_missing=True)
        material = {"CNC": "CONC"}.get(str(p.get("MATERIAL") or "").upper(),
                                       p.get("MATERIAL"))
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("UPSTREAMINVERT")), inv_b=_elev(p.get("DOWNSTREAMINVERT")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(material, config.default_roughness),
        ))

    ground_points = []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _elev((f.get("properties") or {}).get("RIMELEVATION"))
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    outfall_points = []
    for f in outfall_feats:
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) >= 2:
            outfall_points.append((c[0], c[1]))

    result = base.assemble_network(
        pipes, outfall_points=outfall_points, ground_points=ground_points, config=config,
    )
    diag = {**result.diagnostics, "city": "kamloops", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points)}
    return base.NetworkResult(network=result.network, diagnostics=diag)
