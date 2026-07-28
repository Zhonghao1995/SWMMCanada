"""City of Greater Sudbury drainage/wastewater open data -> SWMM ``NetworkIn``
(geometry-inferred topology, tier-2 Ottawa pattern).

Greater Sudbury (AGOL org ``q3mIlR87lZlZsds3``) publishes ``Drainage_view`` with Gravity
Main (9) carrying ``INVERTUS``/``INVERTDS`` per-end inverts and an ``STYPE`` vocabulary —
the gravity graph takes Storm sewer / Collector (trunk) / Outfall / Tunnel and leaves
ditches, municipal drains and road-crossing culverts on their own layers. No node ids —
endpoint snapping. Maintenance Hole (6) carries ``ELEVATION`` (rim); Discharge (4) is the
outfall layer; Catch Basin (0) seeds. ``wastewater_open_data`` mirrors the schema for the
ADR 0011 tracer. Land: Building Roofline + Land-Use parcels.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``INVERTUS``/``INVERTDS`` = pipe-end INVERTS, m AMSL inside a (200, 420) band —
    0 = missing, and the live feed ships junk (a 958 m invert) the band screens.
  * ``ELEVATION`` (maintenance holes) = rim -> node max depths, plausibility-banded;
    the manholes' mostly-null ``DEPTH`` is not read.
"""
from swmmcanada.sources.cities import base

ORG = "https://services.arcgis.com/q3mIlR87lZlZsds3/arcgis/rest/services"
DRAIN, WASTE = "Drainage_view", "wastewater_open_data"
D_MAINS, D_MANHOLES, D_DISCHARGE, D_CATCHBASINS = 9, 6, 4, 0
W_MAINS, W_MANHOLES = 10, 6
BUILDINGS_SVC, BUILDINGS_LYR = "Address_and_Building_Roofline", 3
PARCELS_SVC, PARCELS_LYR = "Land_Use_and_Boundaries_view", 6

SUDBURY_CRS = "EPSG:32617"  # UTM 17N (metric ops)
_PAGE = 2000

_STORM_WHERE = "STYPE IN ('Storm sewer','Collector (trunk)','Outfall','Tunnel')"


SudburyClient = base.ArcGISClient


def _fetch(svc, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ORG}/{svc}/FeatureServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_sudbury_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SudburyClient()
    return {
        "mains": _fetch(DRAIN, D_MAINS, bbox, client, where=_STORM_WHERE),
        "manholes": _fetch(DRAIN, D_MANHOLES, bbox, client),
        "outfalls": _fetch(DRAIN, D_DISCHARGE, bbox, client),
    }


def fetch_sudbury_sanitary(bbox, *, client=None) -> dict:
    """Wastewater gravity system — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SudburyClient()
    return {
        "mains": _fetch(WASTE, W_MAINS, bbox, client),
        "manholes": _fetch(WASTE, W_MANHOLES, bbox, client),
    }


def fetch_sudbury_land(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SudburyClient()
    return {
        "catchbasins": _fetch(DRAIN, D_CATCHBASINS, bbox, client),
        "parcels": _fetch(PARCELS_SVC, PARCELS_LYR, bbox, client),
        "buildings": _fetch(BUILDINGS_SVC, BUILDINGS_LYR, bbox, client),
    }


# Plausible elevation band (m AMSL): Ramsey Lake basin ~230 m to the hilltops ~360 m.
# Applied to INVERTS TOO — the live feed ships junk like a 958 m invert (a fat-fingered
# 258?), which un-banded poisoned the min-invert node logic on the fixture.
_ELEV_MIN, _ELEV_MAX = 200.0, 420.0


def _elev(v):
    """Invert/rim -> float m AMSL inside the plausibility band, or None (0 = missing)."""
    f = base.num(v, zero_missing=True)
    return f if (f is not None and _ELEV_MIN <= f <= _ELEV_MAX) else None


_rim = _elev


_line_ends = base.line_ends

_SUDBURY_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_sudbury_network(data, *, config: base.AssembleConfig = _SUDBURY_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes (no node ids); maintenance-hole rims; discharge-point outfalls."""
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
        name = str(p.get("ASSETID") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('OBJECTID')}"
        w_mm = base.num(p.get("WIDTH"), zero_missing=True)
        material = {"CL": "VC"}.get(str(p.get("MATERIAL") or "").upper(), p.get("MATERIAL"))
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("INVERTUS")), inv_b=_elev(p.get("INVERTDS")),
            diameter_m=(w_mm / 1000.0) if w_mm else None,
            roughness_n=base.material_roughness(material, config.default_roughness),
        ))

    ground_points = []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _rim((f.get("properties") or {}).get("ELEVATION"))
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
    diag = {**result.diagnostics, "city": "sudbury", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points)}
    return base.NetworkResult(network=result.network, diagnostics=diag)
