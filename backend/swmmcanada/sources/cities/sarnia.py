"""City of Sarnia storm/sanitary open data -> SWMM ``NetworkIn`` (geometry topology,
labelled ends).

Sarnia (services1.arcgis.com/ICybsLmBXrZCZV3x, one FeatureServer per dataset) publishes
Storm Sewers (layer 1), Sanitary Sewers (0), Catch Basins (1) and Buildings (2).
``Lifecycle_Status='Active'`` gates both sewer systems, which share one schema:
``UpStreamIn``/``DownStream`` per-end inverts (aliases "UpStreamIn Invert"/"DownStream
Invert"; ~89% populated downtown), ``MH_Upstream``/``MH_Downstream`` id labels, ``Diam_m``
as TEXT millimetres ("1200"), spelled-out ``Material``. No manhole layer with elevations is
published — node max depths keep the assembler default. No parcel layer (buildings only).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UpStreamIn``/``DownStream`` = pipe-end INVERTS, m AMSL inside a (150, 250) band
    (~175-190 across Sarnia; 0 = missing sentinel, and one junk ~108 m row exists
    city-wide — both screened).
"""
from swmmcanada.sources.cities import base

ORG = "https://services1.arcgis.com/ICybsLmBXrZCZV3x/arcgis/rest/services"
STORM = ("Storm_Sewers_Open_Data", 1)
SANITARY = ("Sanitary_Sewers_Open_Data", 0)
CATCHBASINS = ("Catch_Basins_Open_Data", 1)
BUILDINGS = ("Buildings_Open_Data", 2)

SARNIA_CRS = "EPSG:32617"  # UTM 17N (metric ops)
_PAGE = 2000

_ACTIVE_WHERE = "Lifecycle_Status='Active'"


SarniaClient = base.ArcGISClient


def _fetch(svc_layer, bbox, client, where="1=1") -> list:
    svc, layer = svc_layer
    return base.fetch_paged(client, f"{ORG}/{svc}/FeatureServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_sarnia_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SarniaClient()
    return {"mains": _fetch(STORM, bbox, client, where=_ACTIVE_WHERE)}


def fetch_sarnia_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary sewers — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SarniaClient()
    return {"mains": _fetch(SANITARY, bbox, client, where=_ACTIVE_WHERE)}


def fetch_sarnia_land(bbox, *, client=None) -> dict:
    """Catch basins + buildings; Sarnia publishes no parcel polygons."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SarniaClient()
    return {
        "catchbasins": _fetch(CATCHBASINS, bbox, client),
        "parcels": [],
        "buildings": _fetch(BUILDINGS, bbox, client),
    }


# Plausible invert band (m AMSL): Sarnia's terrain sits ~175-190 m; city-wide the active
# inverts all fall in 160-200 with exactly one junk row at ~108 — the band screens it.
_INVERT_MIN, _INVERT_MAX = 150.0, 250.0


def _elev(v):
    """Invert -> float m AMSL inside the plausibility band, or None (0 = missing)."""
    f = base.num(v, zero_missing=True)
    return f if (f is not None and _INVERT_MIN <= f <= _INVERT_MAX) else None


def _diameter_m(text):
    """Diam_m is text millimetres ('1200') -> metres; unparseable -> None."""
    f = base.num(text, zero_missing=True)
    return (f / 1000.0) if f else None


_line_ends = base.line_ends

_SARNIA_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_sarnia_network(data, *, config: base.AssembleConfig = _SARNIA_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes; MH_Upstream/MH_Downstream label the snapped nodes."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)

    pipes, label_points = [], []
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
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("UpStreamIn")), inv_b=_elev(p.get("DownStream")),
            diameter_m=_diameter_m(p.get("Diam_m")),
            roughness_n=base.material_roughness(
                str(p.get("Material") or "").replace("Reinforced ", ""), config.default_roughness),
        ))
        for xy, tid in ((a, p.get("MH_Upstream")), (b, p.get("MH_Downstream"))):
            tid = str(tid or "").strip()
            if tid:
                label_points.append((xy, tid))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(pipes, label_points=label_points, config=config)
    diag = {**result.diagnostics, "city": "sarnia", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom,
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
