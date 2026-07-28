"""City of Delta drainage/sanitary open data -> SWMM ``NetworkIn`` (geometry-inferred
topology with grounds-on-row).

Delta (opendata-deltabc.hub.arcgis.com -> AGOL org ``w2mu7sRltY6PiQ7J``) publishes
Drainage_Mains whose rows carry BOTH the invert levels (``START_IL``/``END_IL``, 76%
city-wide) AND the ground levels (``START_GL``/``END_GL``) at each end — grounds-on-row,
so max depths need no manhole layer (none is published). The missing sentinel is **-99**;
genuine sub-zero inverts exist near sea level (to −3.65 m, verified), so the screen is
``> -90``, NOT ``> 0``. ``START/END_NODE`` fields exist but are empty city-wide — endpoint
snapping. The layer is STORED in EPSG:4326 (``Shape__Length`` is degrees — useless), and
the city's ``ELEV_NOTE`` declares the vertical datum as **CVD28GVRD2018** (the Metro
Vancouver-adjusted CVD28), not CGVD2013 — no datum shim is applied in this first cut
(the model is internally consistent; recorded in DATA.md, ADR 0025 note).
Sanitary_Gravity_Mains mirror with ``START/END_INVELEV``. Land: Property_Parcels (no
catch-basin or building layers — parcels only).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``START_IL``/``END_IL`` (drainage) and ``START_INVELEV``/``END_INVELEV`` (sanitary)
    = pipe-end INVERTS, m CVD28GVRD2018; -99 = missing; negatives near the Fraser are real.
  * ``START_GL``/``END_GL`` = ground levels at the pipe ends -> node max depths.
"""
from swmmcanada.sources.cities import base

ORG = "https://services9.arcgis.com/w2mu7sRltY6PiQ7J/arcgis/rest/services"
MAINS, SAN_MAINS, PARCELS = "Drainage_Mains", "Sanitary_Gravity_Mains", "Property_Parcels"

DELTA_CRS = "EPSG:32610"  # UTM 10N (metric ops)
_PAGE = 2000


DeltaClient = base.ArcGISClient


def _fetch(svc, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ORG}/{svc}/FeatureServer/0/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_delta_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or DeltaClient()
    return {"mains": _fetch(MAINS, bbox, client)}


def fetch_delta_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary gravity mains — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or DeltaClient()
    return {"mains": _fetch(SAN_MAINS, bbox, client)}


def fetch_delta_land(bbox, *, client=None) -> dict:
    """Parcels only — Delta publishes no catch-basin or building-footprint layers."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or DeltaClient()
    return {
        "catchbasins": [],
        "parcels": _fetch(PARCELS, bbox, client),
        "buildings": [],
    }


def _elev(v):
    """Elevation -> float m (CVD28GVRD2018), or None. The sentinel is -99 and genuine
    negatives exist near sea level (to -3.65 m), so screen ``> -90`` — never ``> 0``."""
    f = base.num(v)
    return f if (f is not None and f > -90.0) else None


_line_ends = base.line_ends

_DELTA_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_delta_network(data, *, config: base.AssembleConfig = _DELTA_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes with inverts AND per-end ground levels off the rows (schema
    auto-detected: drainage START_IL vs sanitary START_INVELEV)."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)

    pipes, ground_points = [], []
    n_no_geom = 0
    seen_names: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        inv_a = _elev(p.get("START_IL", p.get("START_INVELEV")))
        inv_b = _elev(p.get("END_IL", p.get("END_INVELEV")))
        name = str(p.get("PIPE_ID") or p.get("GID") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('OBJECTID')}"
        dia_mm = base.num(p.get("PIPE_DIA"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b, inv_a=inv_a, inv_b=inv_b,
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("PIPE_MATRL"), config.default_roughness),
            length_m=base.num(p.get("GRND_LENGTH"), zero_missing=True),
        ))
        for xy, gl in ((a, p.get("START_GL")), (b, p.get("END_GL"))):
            g = _elev(gl)
            if g is not None and g > 0:
                ground_points.append((xy, g))

    result = base.assemble_network(pipes, ground_points=ground_points, config=config)
    diag = {**result.diagnostics, "city": "delta", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_grounds_in": len(ground_points)}
    return base.NetworkResult(network=result.network, diagnostics=diag)
