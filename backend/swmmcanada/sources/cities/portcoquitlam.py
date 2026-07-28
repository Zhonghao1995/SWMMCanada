"""City of Port Coquitlam drainage/sanitary open data -> SWMM ``NetworkIn``
(geometry-inferred topology, tier-2 Ottawa pattern).

Port Coquitlam (data-poco.hub.arcgis.com -> AGOL org ``nz97KciUs5nOw64q``) publishes
``Drainage_Network`` with StmMains (0) carrying ``From_Elev_m``/``To_Elev_m`` per-end
inverts — the missing sentinel is **-99** (Delta's convention; the Pitt/Fraser lowland
sits >= ~1 m, but the ``> -90`` screen is kept for symmetry) — plus STRING
``Diameter_mm``. No node ids. StmManholes (5) carry ``Rim_Elev_m`` (+ ``Bottom_Elev_m``,
deliberately unread); StmBasins (6) seed subcatchments; leads/culverts stay on their own
layers. ``Sanitary_Network2`` SanMains (0) mirrors the schema for the ADR 0011 tracer.
Buildings + Cadastral parcels complete the land kit. The coverage box NESTS inside
Coquitlam's (fourth production nesting — smallest-box dispatch).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``From_Elev_m``/``To_Elev_m`` = pipe-end INVERTS, m AMSL; -99 = missing.
  * ``Rim_Elev_m`` (manholes) = rim -> node max depths, plausibility-banded.
"""
from swmmcanada.sources.cities import base

ORG = "https://services9.arcgis.com/nz97KciUs5nOw64q/arcgis/rest/services"
STM_MAINS, STM_MANHOLES, STM_BASINS = 0, 5, 6
SAN_MAINS = 0

POCO_CRS = "EPSG:32610"  # UTM 10N (metric ops)
_PAGE = 2000


PoCoClient = base.ArcGISClient


def _fetch(svc, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ORG}/{svc}/FeatureServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_portcoquitlam_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or PoCoClient()
    return {
        "mains": _fetch("Drainage_Network", STM_MAINS, bbox, client),
        "manholes": _fetch("Drainage_Network", STM_MANHOLES, bbox, client),
    }


def fetch_portcoquitlam_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary mains — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or PoCoClient()
    return {"mains": _fetch("Sanitary_Network2", SAN_MAINS, bbox, client)}


def fetch_portcoquitlam_land(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or PoCoClient()
    return {
        "catchbasins": _fetch("Drainage_Network", STM_BASINS, bbox, client),
        "parcels": _fetch("Cadastral", 0, bbox, client),
        "buildings": _fetch("Buildings", 0, bbox, client),
    }


def _elev(v):
    """Elevation -> float m AMSL, or None (-99 = missing; kept symmetric with Delta)."""
    f = base.num(v)
    return f if (f is not None and f > -90.0) else None


# Plausible rim band (m AMSL): diked lowland ~1 m to the northern slopes ~160 m.
_RIM_MIN, _RIM_MAX = 0.5, 250.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_line_ends = base.line_ends

_POCO_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_portcoquitlam_network(data, *, config: base.AssembleConfig = _POCO_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes (string diameters; no node ids); manhole rims for depths."""
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
        name = str(p.get("Asset_ID") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('OBJECTID')}"
        dia_mm = base.num(p.get("Diameter_mm"), zero_missing=True)   # STRING mm
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("From_Elev_m")), inv_b=_elev(p.get("To_Elev_m")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("Material"), config.default_roughness),
        ))

    ground_points = []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _rim((f.get("properties") or {}).get("Rim_Elev_m"))
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    result = base.assemble_network(pipes, ground_points=ground_points, config=config)
    diag = {**result.diagnostics, "city": "portcoquitlam", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points)}
    return base.NetworkResult(network=result.network, diagnostics=diag)
