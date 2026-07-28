"""City of Nanaimo storm/sanitary open data -> SWMM ``NetworkIn`` (geometry topology,
labelled ends, rims-on-row).

Nanaimo (data.nanaimo.ca -> AGOL org ``D2GiQOd2jzaj2Pzh``) publishes per-service feature
layers. Storm Sewer Main (7) mixes asset types, so the gravity graph filters
``FTYPE IN ('Main','Culvert')`` — Catch Basin Leads and Perforated Drains stay out; the
sanitary twin uses ``FTYPE='Gravity'`` (Pressure mains out). The pipe rows carry BOTH the
inverts AND the ground elevations: ``ST_INVERT``/``END_INVERT`` plus ``ST_COVELV``/
``END_COVELV`` (cover elevations), so node max depths come straight off the pipe ends —
no manhole join needed. ``ST_NODE``/``END_NODE`` numeric ids label the nodes (MH-prefixed).
The Inlet/Outlet point layer does NOT distinguish inlets from outlets (the Barrie headwall
lesson: an inlet accepted as an outfall forces flow uphill), so it is not used — with real
inverts the per-component sinks land on the true low points at the shore. Land kit:
Storm Sewer Catchbasin (2) seeds + ParcelMap BC Parcel Polygon (4) + Building Footprints (1).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``ST_INVERT``/``END_INVERT`` = pipe-end INVERTS, m AMSL (sea level to ~200 m benches);
    0 = missing.
  * ``ST_COVELV``/``END_COVELV`` = cover/ground elevations at the pipe ends -> node max
    depths (rims-on-row, like White Rock), plausibility-banded.
"""
from swmmcanada.sources.cities import base

ORG = "https://services1.arcgis.com/D2GiQOd2jzaj2Pzh/arcgis/rest/services"
STORM_MAINS = ("Storm_Sewer_Main", 7)
STORM_CATCHBASINS = ("Storm_Sewer_Catchbasin", 2)
SAN_MAINS = ("Sanitary_Sewer_Main", 9)
PARCELS = ("Parcel_Map_BC_Parcel_Polygon", 4)
BUILDINGS = ("Building_Footprints", 1)

NANAIMO_CRS = "EPSG:32610"  # UTM 10N (metric ops)
_PAGE = 2000

_STORM_WHERE = "FTYPE IN ('Main','Culvert')"
_SAN_WHERE = "FTYPE='Gravity'"


NanaimoClient = base.ArcGISClient


def _fetch(svc_layer, bbox, client, where="1=1") -> list:
    svc, layer = svc_layer
    return base.fetch_paged(client, f"{ORG}/{svc}/FeatureServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_nanaimo_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or NanaimoClient()
    return {"mains": _fetch(STORM_MAINS, bbox, client, where=_STORM_WHERE)}


def fetch_nanaimo_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary gravity mains — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or NanaimoClient()
    return {"mains": _fetch(SAN_MAINS, bbox, client, where=_SAN_WHERE)}


def fetch_nanaimo_land(bbox, *, client=None) -> dict:
    """Catchbasins + ParcelMap BC parcels + building footprints (full ADR 0005 kit)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or NanaimoClient()
    return {
        "catchbasins": _fetch(STORM_CATCHBASINS, bbox, client),
        "parcels": _fetch(PARCELS, bbox, client),
        "buildings": _fetch(BUILDINGS, bbox, client),
    }


def _elev(v):
    """Invert/cover -> float m AMSL, or None (0 = missing; the harbour flats sit >= ~1 m)."""
    return base.num(v, zero_missing=True)


# Plausible cover band (m AMSL): harbour shore ~1 m to the upland benches ~250 m.
_COV_MIN, _COV_MAX = 0.5, 350.0


def _cover(v):
    f = _elev(v)
    return f if (f is not None and _COV_MIN <= f <= _COV_MAX) else None


_line_ends = base.line_ends

_NANAIMO_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_nanaimo_network(data, *, config: base.AssembleConfig = _NANAIMO_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes with inverts AND cover elevations read off the pipe rows;
    ST/END_NODE numeric ids (MH-prefixed) label the snapped nodes."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)

    pipes, label_points, ground_points = [], [], []
    n_no_geom = 0
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        dia_mm = base.num(p.get("PIPESIZE"), zero_missing=True)
        material = str(p.get("MATERIAL") or "").replace("Polyvinyl Chloride", "PVC")
        pipes.append(base.RawPipe(
            name=str(p.get("SEQ_ID") or p.get("OBJECTID")),
            end_a=a, end_b=b,
            inv_a=_elev(p.get("ST_INVERT")), inv_b=_elev(p.get("END_INVERT")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(material, config.default_roughness),
            length_m=base.num(p.get("PIPELENGTH"), zero_missing=True),
        ))
        for xy, tid in ((a, p.get("ST_NODE")), (b, p.get("END_NODE"))):
            tid = str(tid or "").strip()
            if tid:
                label_points.append((xy, f"MH{tid}"))
        for xy, cov in ((a, p.get("ST_COVELV")), (b, p.get("END_COVELV"))):
            c = _cover(cov)
            if c is not None:
                ground_points.append((xy, c))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, ground_points=ground_points, label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "nanaimo", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_covers_in": len(ground_points),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
