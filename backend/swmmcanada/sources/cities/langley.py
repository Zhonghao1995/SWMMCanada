"""Township of Langley drainage/sanitary open data -> SWMM ``NetworkIn`` (geometry-inferred
topology, tier-2 Ottawa pattern).

The Township (data.tol.ca -> AGOL org ``frpHL0Fv8koQRVWY``; NOT the separate City of
Langley) publishes one FeatureServer per dataset: Drainage_Pipes with
``Upstream/Downstream_Elevation`` (90% city-wide — the nulls cluster at the START of the
table, so never judge coverage from the first page), ``Pipe_Type_txt`` (Gravity/...),
STRING ``Diameter`` and ``Lifecycle_Status`` (Asbuilt / Preliminary - Constructed live;
Decommissioned/Abandoned out). No node-id fields — endpoint snapping. Drainage_Manholes
carry ``Manhole_RimElev``; Drainage_Sources are the inlet/seed points; Sanitary_* mirror
the schema; Parcels ride their own service (licence: tol.ca/opengovlicense).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``Upstream/Downstream_Elevation`` (pipes) = pipe-end INVERTS, m AMSL (~5-120 across
    the uplands); 0 = missing.
  * ``Manhole_RimElev`` = rim -> node max depths, banded; ``Manhole_Depth`` not read.
"""
from swmmcanada.sources.cities import base

ORG = "https://services5.arcgis.com/frpHL0Fv8koQRVWY/arcgis/rest/services"
PIPES, MANHOLES, SOURCES = "Drainage_Pipes", "Drainage_Manholes", "Drainage_Sources"
SAN_PIPES, SAN_MANHOLES = "Sanitary_Pipes", "Sanitary_Manholes"
PARCELS = "Parcels"

LANGLEY_CRS = "EPSG:32610"  # UTM 10N (metric ops)
_PAGE = 1000

_LIVE_WHERE = "Lifecycle_Status IN ('Asbuilt','Preliminary - Constructed')"


LangleyClient = base.ArcGISClient


def _fetch(svc, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ORG}/{svc}/FeatureServer/0/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_langley_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or LangleyClient()
    return {
        "mains": _fetch(PIPES, bbox, client, where=_LIVE_WHERE),
        "manholes": _fetch(MANHOLES, bbox, client, where=_LIVE_WHERE),
    }


def fetch_langley_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary pipes — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or LangleyClient()
    return {
        "mains": _fetch(SAN_PIPES, bbox, client, where=_LIVE_WHERE),
        "manholes": _fetch(SAN_MANHOLES, bbox, client, where=_LIVE_WHERE),
    }


def fetch_langley_land(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or LangleyClient()
    return {
        "catchbasins": _fetch(SOURCES, bbox, client),
        "parcels": _fetch(PARCELS, bbox, client),
        "buildings": [],
    }


def _elev(v):
    """Invert/rim -> float m AMSL, or None (0 = missing; the lowlands sit >= ~2 m)."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): Fraser lowland ~2 m to the uplands ~130 m.
_RIM_MIN, _RIM_MAX = 0.5, 200.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_line_ends = base.line_ends

_LANGLEY_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_langley_network(data, *, config: base.AssembleConfig = _LANGLEY_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes (string diameters parsed; no node ids); manhole rims for depths."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    manholes = _features((data or {}).get("manholes", []) if isinstance(data, dict) else [])

    pipes = []
    n_no_geom = 0
    seen_names: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        name = str(p.get("Id_Pipe") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('OBJECTID')}"
        dia_mm = base.num(p.get("Diameter"), zero_missing=True)   # STRING mm ("250")
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("Upstream_Elevation")), inv_b=_elev(p.get("Downstream_Elevation")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=config.default_roughness,   # no material field is published
            length_m=base.num(p.get("Length_Asbuilt"), zero_missing=True),
        ))

    ground_points = []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _rim((f.get("properties") or {}).get("Manhole_RimElev"))
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    result = base.assemble_network(pipes, ground_points=ground_points, config=config)
    diag = {**result.diagnostics, "city": "langley", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points)}
    return base.NetworkResult(network=result.network, diagnostics=diag)
