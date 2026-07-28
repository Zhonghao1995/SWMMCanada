"""City of Toronto sewer data -> SWMM ``NetworkIn`` (geometry topology, labelled ends).

Toronto Water publishes official EXTERNAL VIEW feature services on the city's AGOL org
(``services5.arcgis.com/MFwjjnaTnj9B3bil``, owner GCC_TWAGO) — the same data behind the
open-data portal's daily-updated *Sewer Gravity Mains* CKAN package, but with real spatial
queries, so Toronto rides the standard ArcGIS adapter path (no CKAN datastore layer needed).
City-wide: 166,247 gravity mains, of which 81k storm + 13k combined.

``WATERTYPE`` splits the systems: the storm graph takes ``('Storm','Combined')`` — downtown
Toronto is heavily combined and combined pipes carry the stormwater (ADR 0021, the
Vancouver/Ottawa decision) — while ``'SAN'`` is the separated-sanitary tracer (ADR 0011).
CSO/SCSO/EO relief structures and FD foundation drains stay out of both. ``FROMMH``/``TOMH``
label the snapped endpoint nodes with real maintenance-hole ids (``MH5471324810``); Sewer
Manhole Ext View carries ``RIMELEV``; Sewer Discharge Point Ext View is the outfall layer
(1,914 city-wide, waterfront/valley edges); Sewer Inlet Ext View seeds subcatchments. No
parcel/building layers ride this org — imperviousness falls back to land cover with
catch-basin tessellation (Ottawa-style).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UPELEV``/``DOWNELEV`` (gravity mains) = pipe-end INVERTS, m AMSL (~74 lakefront to
    ~200 m uptown); 0 = missing.
  * ``RIMELEV`` (manholes) = rim -> node max depths, plausibility-banded.
"""
from swmmcanada.sources.cities import base

ORG = "https://services5.arcgis.com/MFwjjnaTnj9B3bil/arcgis/rest/services"
MAINS_SVC = "COT_Geospatial_TW_Sewer_Gravity_Main_Ext_View"
MANHOLES_SVC = "COT_Geospatial_TW_Sewer_Manhole_Ext_View"
DISCHARGE_SVC = "COT_Geospatial_TW_Sewer_Discharge_Point_Ext_View"
INLETS_SVC = "COT_Geospatial_TW_Sewer_Inlet_Ext_View"

TORONTO_CRS = "EPSG:32617"  # UTM 17N (metric ops)
_PAGE = 2000

# ADR 0021: combined mains carry the stormwater and join the storm graph; the sanitary
# tracer stays SAN-only so nothing is double-counted. CSO/SCSO/EO relief pipes and FD
# foundation drains belong to neither gravity graph.
_STORM_WHERE = "WATERTYPE IN ('Storm','Combined')"
_SAN_WHERE = "WATERTYPE='SAN'"


TorontoClient = base.ArcGISClient


def _fetch(service, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ORG}/{service}/FeatureServer/0/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_toronto_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or TorontoClient()
    return {
        "mains": _fetch(MAINS_SVC, bbox, client, where=_STORM_WHERE),
        "manholes": _fetch(MANHOLES_SVC, bbox, client),
        "outfalls": _fetch(DISCHARGE_SVC, bbox, client),
    }


def fetch_toronto_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary mains — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or TorontoClient()
    return {
        "mains": _fetch(MAINS_SVC, bbox, client, where=_SAN_WHERE),
        "manholes": _fetch(MANHOLES_SVC, bbox, client),
    }


def fetch_toronto_land(bbox, *, client=None) -> dict:
    """Sewer inlets as subcatchment seeds; no parcel/building layers on the TW org."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or TorontoClient()
    return {
        "catchbasins": _fetch(INLETS_SVC, bbox, client),
        "parcels": [],
        "buildings": [],
    }


def _elev(v):
    """Invert/rim -> float m AMSL, or None (0 = missing; Toronto sits ~74-210 m)."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): Lake Ontario shore ~74 m to the northern uplands ~210 m.
_RIM_MIN, _RIM_MAX = 60.0, 300.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_line_ends = base.line_ends

_TORONTO_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_toronto_network(data, *, config: base.AssembleConfig = _TORONTO_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes from gravity mains, FROMMH/TOMH as node labels, rims from manholes,
    outfalls from discharge points. WATERTYPE counts land in diagnostics."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    manholes = _features((data or {}).get("manholes", []) if isinstance(data, dict) else [])
    outfall_feats = _features((data or {}).get("outfalls", []) if isinstance(data, dict) else [])

    pipes, label_points = [], []
    n_no_geom = 0
    watertype_hist: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        wt = str(p.get("WATERTYPE") or "?")
        watertype_hist[wt] = watertype_hist.get(wt, 0) + 1
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
                label_points.append((xy, tid))

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
    diag = {**result.diagnostics, "city": "toronto", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "watertype_histogram": watertype_hist,
            "n_combined_included": watertype_hist.get("Combined", 0),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
