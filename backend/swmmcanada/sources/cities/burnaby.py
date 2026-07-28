"""City of Burnaby storm/sanitary open data -> SWMM ``NetworkIn`` (geometry topology,
labelled ends).

Burnaby serves its open data from the city's own ArcGIS Server (gis.burnaby.ca,
``OpenData`` folder; Open Government Licence – Burnaby). ``OpenData2`` carries the sewer
kit: Storm Main (18) with ``UPSELEV``/``DWNELEV`` per-end inverts (65% city-wide),
``UNITID``/``UNITID2`` node labels (``DM…``), ``PIPEDIAM`` mm + ``PIPETYPE`` material +
``PIPESHP``/``PIPEHT`` sections; Catchbasin (19) seeds subcatchments; Sanitary Main (10)
shares the schema for the ADR 0011 tracer. ``OpenData4`` has Legal Parcels (7) and
Building Outlines (18) for the full ADR 0005 land kit. ``SERVSTAT='I'`` (or null — most
rows carry no status) keeps RMVD/ABND/DRMV lines out. Storm Fitting publishes only a
depth (``MHDPTH``), no rim elevation, so node max depths keep the assembler default
(future enrichment: London-style depth backfill).

Coverage note: the registry box cedes two boundary slivers to neighbours — Boundary Road
(west, to Vancouver's box) and North Road (east, to Coquitlam's) — so the metro boxes stay
disjoint; New Westminster's box later NESTS inside this one (smallest-box dispatch).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UPSELEV``/``DWNELEV`` = pipe-end INVERTS, m AMSL (sea level to ~370 m on Burnaby
    Mountain); 0 = missing.
  * ``UPSDPTH``/``DWNDPTH`` (depths) and fitting ``MHDPTH`` are deliberately not read.
"""
from swmmcanada.sources.cities import base

ARC = "https://gis.burnaby.ca/arcgis/rest/services/OpenData"
OD2, OD4 = "OpenData2", "OpenData4"
STORM_MAINS, STORM_CATCHBASINS, SAN_MAINS = 18, 19, 10
PARCELS, BUILDINGS = 7, 18

BURNABY_CRS = "EPSG:32610"  # UTM 10N (metric ops)
_PAGE = 1000                # layer maxRecordCount

# 'I' = in service; most rows carry NULL status (equally live). RMVD/ABND/DRMV stay out.
_LIVE_WHERE = "SERVSTAT='I' OR SERVSTAT IS NULL"


BurnabyClient = base.ArcGISClient


def _fetch(service, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ARC}/{service}/MapServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_burnaby_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or BurnabyClient()
    return {"mains": _fetch(OD2, STORM_MAINS, bbox, client, where=_LIVE_WHERE)}


def fetch_burnaby_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary mains — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or BurnabyClient()
    return {"mains": _fetch(OD2, SAN_MAINS, bbox, client, where=_LIVE_WHERE)}


def fetch_burnaby_land(bbox, *, client=None) -> dict:
    """Catchbasins + Legal Parcels + Building Outlines (full ADR 0005 kit)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or BurnabyClient()
    return {
        "catchbasins": _fetch(OD2, STORM_CATCHBASINS, bbox, client),
        "parcels": _fetch(OD4, PARCELS, bbox, client),
        "buildings": _fetch(OD4, BUILDINGS, bbox, client),
    }


def _elev(v):
    """Invert -> float m AMSL, or None (0 = missing; the Fraser flats sit >= ~2 m)."""
    return base.num(v, zero_missing=True)


_line_ends = base.line_ends

_BURNABY_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_burnaby_network(data, *, config: base.AssembleConfig = _BURNABY_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes from mains (storm/sanitary share the schema), UNITID/UNITID2 as
    node labels; PIPESHP + PIPEHT/PIPEDIAM ride the #130 shape mapping."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)

    pipes, label_points = [], []
    n_no_geom = 0
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        dia_mm = base.num(p.get("PIPEDIAM"), zero_missing=True)
        h_mm = base.num(p.get("PIPEHT"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=str(p.get("COMPKEY") or p.get("OBJECTID")),
            end_a=a, end_b=b,
            inv_a=_elev(p.get("UPSELEV")), inv_b=_elev(p.get("DWNELEV")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("PIPETYPE"), config.default_roughness),
            length_m=base.num(p.get("PIPELEN"), zero_missing=True),
            shape=p.get("PIPESHP"),
            height_m=(h_mm / 1000.0) if h_mm else None,
            width_m=(dia_mm / 1000.0) if dia_mm else None,
        ))
        for xy, tid in ((a, p.get("UNITID")), (b, p.get("UNITID2"))):
            tid = str(tid or "").strip()
            if tid:
                label_points.append((xy, tid))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(pipes, label_points=label_points, config=config)
    diag = {**result.diagnostics, "city": "burnaby", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom,
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
