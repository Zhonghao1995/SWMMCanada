"""City of Abbotsford drainage open data -> SWMM ``NetworkIn`` (geometry topology, labelled ends).

Abbotsford (opendata-abbotsford.hub.arcgis.com -> AGOL org ``ZYlQy38aWlfDG1Qh``) publishes
everything inside ONE monolithic ``Engineering_Layers_External_Feature`` FeatureServer with
200+ layers — layer IDs matter, not service names: Drainage Mains (207), Drainage Manholes
(204), Drainage Outlets (198), Drainage Catchbasins (205), Sanitary Mains (214) and
Sanitary Manholes (212). Parcels live on ``Parcel_Layers_External_Feature/0``; no public
building-footprint layer exists (imperviousness falls back to land cover, Kitchener-style).

Attribute values are CODED DOMAINS, not strings: ``LIFECYCLE_STATUS`` 0 = Active (the
gravity-graph filter) and ``MATERIAL`` is an integer code (0=PVC, 1=Concrete, ...) decoded
against the domain table recorded 2026-07-28. ``UPLINK``/``DOWNLINK`` label the snapped
endpoint nodes with real link ids (``1059C8``); the literal string ``'N/A'`` means absent.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UPSTREAM_INVERT``/``DOWNSTREAM_INVERT`` (mains, storm + sanitary) = pipe-end INVERTS,
    m AMSL; the city uses BOTH ``0`` and ``-1`` as missing sentinels, so any value <= 0 is
    treated as missing (the Fraser/Sumas lowland sits >= ~2 m, so no real invert is lost).
  * ``RIM_ELEVATION`` (manholes) = ground/rim -> node max depths, plausibility-banded.
"""
from swmmcanada.sources.cities import base

ORG = "https://services8.arcgis.com/ZYlQy38aWlfDG1Qh/arcgis/rest/services"
ENG = f"{ORG}/Engineering_Layers_External_Feature/FeatureServer"
PAR = f"{ORG}/Parcel_Layers_External_Feature/FeatureServer"
STORM_MAINS, STORM_MANHOLES, STORM_OUTLETS, STORM_CATCHBASINS = 207, 204, 198, 205
SAN_MAINS, SAN_MANHOLES = 214, 212
PARCELS = 0

ABBOTSFORD_CRS = "EPSG:32610"  # UTM 10N (metric ops)
_PAGE = 2000

# LIFECYCLE_STATUS domain (recorded 2026-07-28): 0=Active, 1=Abandoned, 2=Inactive,
# 3=Future, 4=Not Verified, 5=Under Construction, 6=Temporary, 7=In Progress, 8=Surveyed.
_ACTIVE_WHERE = "LIFECYCLE_STATUS=0"

# MATERIAL coded-value domain (recorded 2026-07-28) -> shared roughness codes.
_MATERIAL_CODES = {0: "PVC", 1: "CONC", 2: "VC", 3: "AC", 5: "CSP", 6: "HDPE",
                   7: "DI", 8: "STL", 11: "PVC", 13: "PVC"}   # CIPP/PVCO ~ plastic liners


AbbotsfordClient = base.ArcGISClient


def _fetch(service, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{service}/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_abbotsford_storm(bbox, *, client=None) -> dict:
    """Active drainage mains + manholes (rims) + the Drainage Outlets point layer."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or AbbotsfordClient()
    return {
        "mains": _fetch(ENG, STORM_MAINS, bbox, client, where=_ACTIVE_WHERE),
        "manholes": _fetch(ENG, STORM_MANHOLES, bbox, client, where=_ACTIVE_WHERE),
        "outfalls": _fetch(ENG, STORM_OUTLETS, bbox, client),
    }


def fetch_abbotsford_sanitary(bbox, *, client=None) -> dict:
    """Active sanitary mains + manholes — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or AbbotsfordClient()
    return {
        "mains": _fetch(ENG, SAN_MAINS, bbox, client, where=_ACTIVE_WHERE),
        "manholes": _fetch(ENG, SAN_MANHOLES, bbox, client, where=_ACTIVE_WHERE),
    }


def fetch_abbotsford_land(bbox, *, client=None) -> dict:
    """Catchbasins + parcels; Abbotsford publishes no building footprints (buildings [])."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or AbbotsfordClient()
    return {
        "catchbasins": _fetch(ENG, STORM_CATCHBASINS, bbox, client),
        "parcels": _fetch(PAR, PARCELS, bbox, client),
        "buildings": [],
    }


def _invert(v):
    """Pipe-end invert -> float m AMSL, or None. Abbotsford uses BOTH 0 and -1 as missing
    sentinels, so anything <= 0 is missing (the lowland sits >= ~2 m AMSL)."""
    f = base.num(v)
    return f if (f is not None and f > 0) else None


# Plausible rim band (m AMSL): Fraser/Sumas lowland ~2 m to Sumas Mountain ~600 m.
_RIM_MIN, _RIM_MAX = 0.5, 700.0


def _rim(v):
    f = base.num(v, zero_missing=True)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


def _roughness(code, default):
    mapped = _MATERIAL_CODES.get(int(code)) if code is not None and str(code).lstrip("-").isdigit() else None
    return base.material_roughness(mapped, default) if mapped else default


_line_ends = base.line_ends

_ABBOTSFORD_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_abbotsford_network(data, *, config: base.AssembleConfig = _ABBOTSFORD_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes from mains (storm/sanitary share the schema), UPLINK/DOWNLINK as
    node labels ('N/A' = absent), rims from manholes, outfalls from Drainage Outlets."""
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
            name=str(p.get("LINKID") or p.get("ASSET_ID") or p.get("OBJECTID")),
            end_a=a, end_b=b,
            inv_a=_invert(p.get("UPSTREAM_INVERT")), inv_b=_invert(p.get("DOWNSTREAM_INVERT")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=_roughness(p.get("MATERIAL"), config.default_roughness),
        ))
        for xy, tid in ((a, p.get("UPLINK")), (b, p.get("DOWNLINK"))):
            tid = str(tid or "").strip()
            if tid and tid.upper() != "N/A":
                label_points.append((xy, tid))

    ground_points = []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _rim((f.get("properties") or {}).get("RIM_ELEVATION"))
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
    diag = {**result.diagnostics, "city": "abbotsford", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
