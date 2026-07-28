"""City of Coquitlam drainage open data -> SWMM ``NetworkIn`` (geometry topology, labelled ends).

Coquitlam (data.coquitlam.ca -> AGOL org ``Q6Lq3evZUGfPrN7o``) publishes the richest drainage
kit in Metro Vancouver: Drainage Mains carry per-end invert elevations (95% populated
city-wide) plus UP/DN termination ids, and the same FeatureServer has Manholes (rim + chamber
invert), a dedicated Outfalls layer and Catchbasins. Cadastral serves Parcels + Buildings, so
subcatchments use the full ADR 0005 parcel/building method. Licence: Open Government Licence –
Coquitlam.

Topology is assembled from pipe polyline endpoints (coordinate snapping, Ottawa pattern)
rather than the UP/DN_TERM_ID join: termination ids reference many feature classes (MH
manholes, but also PI pipes and VN virtual nodes for mid-run taps), so the ids are used as
node LABELS — nodes keep real city ids like ``STMH19081`` — while geometry stays the
authority on connectivity. ``STATUS='OPERATING'`` keeps MOT/METRO/PRIVATE/ABANDONED/
DECOMMISSIONED/NOT READY lines out of the gravity graph. CB Leads live on their own layer
(15) and are not fetched, so the mains layer is lead-free.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UP_ELEVATION``/``DN_ELEVATION`` (storm mains) and ``UP_ELEV``/``DN_ELEV`` (sanitary
    mains) = pipe-end INVERTS, metres AMSL (sea level to ~336 m on Westwood Plateau);
    0 is the missing sentinel (the diked Fraser lowland sits >=~1 m).
  * ``RIM_ELEVATION`` (drainage manholes) / ``RIM_ELEV`` (sanitary manholes) = ground/rim
    -> node max depths. A 0.0 rim is a placeholder, screened by the plausibility band.
  * ``INV_ELEVATION``/``INVRT_ELEV`` (manholes) is the chamber invert; deliberately NOT
    read — node invert stays the lowest connected pipe-end invert (see ASSUMPTIONS.md).
  * Outfall ``INV_ELEVATION`` exists on layer 10; the assembler's pipe-end-derived outfall
    inverts are kept for the first cut (future enrichment, London-style override).
"""
from swmmcanada.sources.cities import base

ORG = "https://services2.arcgis.com/Q6Lq3evZUGfPrN7o/arcgis/rest/services"
DRAINAGE = f"{ORG}/Drainage%20Utility/FeatureServer"   # service name contains a space
SANITARY = f"{ORG}/Sanitary%20Utility/FeatureServer"
CADASTRAL = f"{ORG}/Cadastral/FeatureServer"
STORM_MAINS = 16        # Drainage Mains (polyline): UP/DN_ELEVATION + UP/DN_TERM_ID
STORM_MANHOLES = 6      # Drainage Manholes (point): RIM_ELEVATION + INV_ELEVATION
STORM_OUTFALLS = 10     # Drainage Outfalls (point)
STORM_CATCHBASINS = 11  # Drainage Catchbasins (point): subcatchment seeds
SAN_MAINS = 10          # Sanitary Mains (polyline): UP/DN_ELEV, DIAMETER
SAN_MANHOLES = 0        # Sanitary Manholes (point): RIM_ELEV
PARCELS = 13            # Cadastral Parcels (polygon)
BUILDINGS = 15          # Cadastral Buildings (polygon)

COQUITLAM_CRS = "EPSG:32610"  # UTM 10N (metric ops) — same zone as Vancouver/Surrey
_PAGE = 2000                  # layer maxRecordCount

# Gravity graph = the city's own in-service lines. Other STATUS values (MOT highway drainage,
# METRO regional, PRIVATE, ABANDONED, DECOMMISSIONED, NOT READY) stay out.
_OPERATING_WHERE = "STATUS='OPERATING'"


CoquitlamClient = base.ArcGISClient


def _fetch(service, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{service}/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_coquitlam_storm(bbox, *, client=None) -> dict:
    """Operating storm mains + manholes (rims) + the dedicated outfall layer."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or CoquitlamClient()
    return {
        "mains": _fetch(DRAINAGE, STORM_MAINS, bbox, client, where=_OPERATING_WHERE),
        "manholes": _fetch(DRAINAGE, STORM_MANHOLES, bbox, client),
        "outfalls": _fetch(DRAINAGE, STORM_OUTFALLS, bbox, client),
    }


def fetch_coquitlam_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary gravity mains + manholes — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or CoquitlamClient()
    return {
        "mains": _fetch(SANITARY, SAN_MAINS, bbox, client, where=_OPERATING_WHERE),
        "manholes": _fetch(SANITARY, SAN_MANHOLES, bbox, client),
    }


def fetch_coquitlam_land(bbox, *, client=None) -> dict:
    """Catchbasins + parcels + buildings for the ADR 0005 subcatchment method."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or CoquitlamClient()
    return {
        "catchbasins": _fetch(DRAINAGE, STORM_CATCHBASINS, bbox, client),
        "parcels": _fetch(CADASTRAL, PARCELS, bbox, client),
        "buildings": _fetch(CADASTRAL, BUILDINGS, bbox, client),
    }


def _elev(v):
    """Invert/rim -> float m AMSL, or None when missing. 0 is the missing sentinel — the
    diked Fraser lowland sits at >=~1 m, so a literal 0.0 is unmeasured, not sea level."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): Fraser lowland ~1 m up to Westwood Plateau ~340 m; a rim
# outside the band is a placeholder and must not poison that node's max depth (#157 band
# still guards downstream, this keeps the obvious junk out at the source).
_RIM_MIN, _RIM_MAX = 0.5, 450.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


# Coquitlam publishes full words, the shared table wants codes.
_MATERIAL_ALIASES = {
    "CONCRETE": "CONC", "REINFORCED CONCRETE": "CONC", "ASBESTOS CEMENT": "AC",
    "CORRUGATED METAL": "CMP", "STAINLESS STEEL": "STL", "STEEL": "STL",
}


def _roughness(material, default):
    code = str(material or "").strip().upper()
    return base.material_roughness(_MATERIAL_ALIASES.get(code, code), default)


_line_ends = base.line_ends

# Endpoints are drawn between structures in the city GIS and coincide well; ~1 m snapping
# (snap_decimals=5) matches the other geometry-assembled adapters (Ottawa/Calgary/Kelowna).
_COQUITLAM_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_coquitlam_network(data, *, config: base.AssembleConfig = _COQUITLAM_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes from mains (storm or sanitary schema — field names auto-detected),
    node labels from UP/DN_TERM_ID, rims from manholes, outfalls from the outfall layer."""
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
        # storm mains: UP_ELEVATION/INSIDE_DIAMETER; sanitary mains: UP_ELEV/DIAMETER
        inv_a = _elev(p.get("UP_ELEVATION", p.get("UP_ELEV")))
        inv_b = _elev(p.get("DN_ELEVATION", p.get("DN_ELEV")))
        dia_mm = base.num(p.get("INSIDE_DIAMETER", p.get("DIAMETER")), zero_missing=True)
        pipes.append(base.RawPipe(
            name=str(p.get("GIS_ID") or p.get("OBJECTID")),
            end_a=a, end_b=b, inv_a=inv_a, inv_b=inv_b,
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=_roughness(p.get("MATERIAL_TYPE"), config.default_roughness),
            length_m=base.num(p.get("LENGTH"), zero_missing=True),
        ))
        # termination ids as node labels (storm only; sanitary mains carry none)
        for xy, tid in ((a, p.get("UP_TERM_ID")), (b, p.get("DN_TERM_ID"))):
            if tid:
                label_points.append((xy, str(tid)))

    ground_points = []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _rim((f.get("properties") or {}).get("RIM_ELEVATION",
                   (f.get("properties") or {}).get("RIM_ELEV")))
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    outfall_points = []
    for f in outfall_feats:
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) >= 2:
            outfall_points.append((c[0], c[1]))

    # A termination id can legitimately appear at endpoints that snap to DIFFERENT nodes
    # (virtual-node ids on pipes, id reuse across features) — the shared label safety drops
    # those so node names stay unique (SWMM case-folds names; N# namespace is reserved).
    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, outfall_points=outfall_points, ground_points=ground_points,
        label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "coquitlam", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
