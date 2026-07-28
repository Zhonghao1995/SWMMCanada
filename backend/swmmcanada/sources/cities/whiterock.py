"""City of White Rock storm/sanitary open data -> SWMM ``NetworkIn`` (geometry topology,
labelled ends, rims-on-row).

White Rock (maps.whiterockcity.ca, ``opendata`` folder — one MapServer per dataset, layer 0)
is the smallest wave-2 city (~5 km²) and one of the richest per-row schemas: Storm Lines
carry ``Us/Ds_Inv_Ele`` inverts AND ``Us/Ds_Rm_E`` rim elevations on the same pipe row
(rims-on-row, the Nanaimo pattern), plus SmallInteger ``Us/Ds_End_Id`` node ids joining the
Storm Manholes layer. ``Line_Type IN ('Pipe','Pi_Dc')`` keeps creeks/ditches/abandoned
lines out of the storm graph; the sanitary twin uses ``Line_Type='Gravity'`` (Force/Aband
out) and shares the ``Us/Ds_*`` schema. No catch-basin layer exists — Storm Manholes seed
the subcatchments instead (documented deviation). Parcel + Building_Outlines complete the
ADR 0005 land kit.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``Us_Inv_Ele``/``Ds_Inv_Ele`` = pipe-end INVERTS, m AMSL (waterfront ~2 m to the
    upland ~120 m); 0 = missing.
  * ``Us_Rm_E``/``Ds_Rm_E`` = rim elevations at the pipe ends -> node max depths,
    plausibility-banded.
"""
from swmmcanada.sources.cities import base

BASE_URL = "https://maps.whiterockcity.ca/server/rest/services/opendata"
STORM_LINES, STORM_MANHOLES = "Storm_Lines", "Storm_Manholes"
SAN_LINES = "Sanitary_Lines"
PARCELS, BUILDINGS = "Parcel", "Building_Outlines"

WHITEROCK_CRS = "EPSG:32610"  # UTM 10N (metric ops)
_PAGE = 2000

_STORM_WHERE = "Line_Type IN ('Pipe','Pi_Dc')"
_SAN_WHERE = "Line_Type='Gravity'"


WhiteRockClient = base.ArcGISClient


def _fetch(svc, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{BASE_URL}/{svc}/MapServer/0/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_whiterock_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or WhiteRockClient()
    return {"mains": _fetch(STORM_LINES, bbox, client, where=_STORM_WHERE)}


def fetch_whiterock_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary gravity lines — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or WhiteRockClient()
    return {"mains": _fetch(SAN_LINES, bbox, client, where=_SAN_WHERE)}


def fetch_whiterock_land(bbox, *, client=None) -> dict:
    """Storm Manholes stand in as seeds (no catch-basin layer exists); parcels + buildings."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or WhiteRockClient()
    return {
        "catchbasins": _fetch(STORM_MANHOLES, bbox, client),
        "parcels": _fetch(PARCELS, bbox, client),
        "buildings": _fetch(BUILDINGS, bbox, client),
    }


def _elev(v):
    """Invert/rim -> float m AMSL, or None (0 = missing; the waterfront sits >= ~2 m)."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): Marine Drive ~2 m to the upland crest ~120 m.
_RIM_MIN, _RIM_MAX = 0.5, 200.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_line_ends = base.line_ends

_WHITEROCK_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_whiterock_network(data, *, config: base.AssembleConfig = _WHITEROCK_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes with inverts and rims read off the pipe rows; SmallInteger
    Us/Ds_End_Id node ids get an MH prefix as labels."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)

    pipes, label_points, ground_points = [], [], []
    n_no_geom = 0
    seen_names: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        dia_mm = base.num(p.get("Us_Pipe_Si"), zero_missing=True) or \
            base.num(p.get("Ds_Pipe_Si"), zero_missing=True)
        name = str(p.get("Storm_Id") or p.get("Sani_Id") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('OBJECTID')}"
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("Us_Inv_Ele")), inv_b=_elev(p.get("Ds_Inv_Ele")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("Us_Pipe_Ty"), config.default_roughness),
        ))
        for xy, tid in ((a, p.get("Us_End_Id")), (b, p.get("Ds_End_Id"))):
            tid = str(tid or "").strip()
            if tid and tid != "0":
                label_points.append((xy, f"MH{tid}"))
        for xy, rim in ((a, p.get("Us_Rm_E")), (b, p.get("Ds_Rm_E"))):
            r = _rim(rim)
            if r is not None:
                ground_points.append((xy, r))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, ground_points=ground_points, label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "whiterock", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
