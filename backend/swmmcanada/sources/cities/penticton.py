"""City of Penticton storm/sanitary open data -> SWMM ``NetworkIn`` (geometry topology,
labelled ends).

Penticton (opendata.penticton.ca -> AGOL org ``ZMQyarkhNAnn8lip``) publishes Utility-
Network-style services with many layers; the ones that matter here: ``Storm_PRD`` Pipe
(415, ``ASSETTYPE='Gravity Pipe'``), Manhole (412), Outlet (410, the outfall layer),
Catchbasin (408); ``Sanitary_PRD`` Sewer Gravity Main (316, ``ASSETTYPE IN Main/Trunk``)
and Manhole (313). ``lifecyclestatus='In Service'`` gates everything. ``us_feat``/
``ds_feat`` termination ids (``SWMH-42``, ``SWDP-231``, ``SWF-625``) label the snapped
nodes. ``diameter`` is TEXT WITH UNITS (``"300 mm"``) and ``material`` is spelled out with
a trailing code (``"Concrete (Non-Reinforced) - CP"``) — both are parsed. Manholes publish
``invertelev``/``highelev`` but NO rim, so node max depths keep the assembler default.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``upelev``/``downelev`` (pipes) = pipe-end INVERTS, m AMSL (Okanagan Lake ~342 m up
    to ~520 m benches); 0 = missing.
  * Manhole ``invertelev`` (chamber) and ``highelev`` (high pipe) are deliberately not
    read (no rim source exists — ASSUMPTIONS.md convention).
"""
import re

from swmmcanada.sources.cities import base

ORG = "https://services1.arcgis.com/ZMQyarkhNAnn8lip/arcgis/rest/services"
STORM_SVC, SAN_SVC = "Storm_PRD", "Sanitary_PRD"
STORM_PIPES, STORM_MANHOLES, STORM_OUTLETS, STORM_CATCHBASINS = 415, 412, 410, 408
SAN_MAINS, SAN_MANHOLES = 316, 313

PENTICTON_CRS = "EPSG:32611"  # UTM 11N (metric ops) — same zone as Kelowna
_PAGE = 2000

_STORM_WHERE = "lifecyclestatus='In Service' AND ASSETTYPE='Gravity Pipe'"
_SAN_WHERE = "lifecyclestatus='In Service' AND ASSETTYPE IN ('Main','Trunk')"


PentictonClient = base.ArcGISClient


def _fetch(svc, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ORG}/{svc}/FeatureServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_penticton_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or PentictonClient()
    return {
        "mains": _fetch(STORM_SVC, STORM_PIPES, bbox, client, where=_STORM_WHERE),
        "outfalls": _fetch(STORM_SVC, STORM_OUTLETS, bbox, client),
    }


def fetch_penticton_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary gravity mains — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or PentictonClient()
    return {"mains": _fetch(SAN_SVC, SAN_MAINS, bbox, client, where=_SAN_WHERE)}


def fetch_penticton_land(bbox, *, client=None) -> dict:
    """Catchbasins as seeds; parcels/buildings live on other org services (not fetched —
    land-cover imperviousness)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or PentictonClient()
    return {
        "catchbasins": _fetch(STORM_SVC, STORM_CATCHBASINS, bbox, client),
        "parcels": [],
        "buildings": [],
    }


def _elev(v):
    """Invert -> float m AMSL, or None (0 = missing; Okanagan Lake sits at ~342 m)."""
    return base.num(v, zero_missing=True)


_MM = re.compile(r"([\d.]+)\s*mm", re.I)


def _diameter_m(text):
    """'300 mm' -> 0.3; unparseable/missing -> None."""
    m = _MM.search(str(text or ""))
    return float(m.group(1)) / 1000.0 if m else None


def _roughness(material, default):
    """Material strings end in a code ('Concrete (Non-Reinforced) - CP' -> CP)."""
    code = str(material or "").rsplit("-", 1)[-1].strip().upper()
    return base.material_roughness(code, default)


_line_ends = base.line_ends

_PENTICTON_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_penticton_network(data, *, config: base.AssembleConfig = _PENTICTON_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes; us_feat/ds_feat termination ids label the snapped nodes; the
    Outlet point layer feeds outfall candidates."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    outfall_feats = _features((data or {}).get("outfalls", []) if isinstance(data, dict) else [])

    pipes, label_points = [], []
    n_no_geom = 0
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        pipes.append(base.RawPipe(
            name=str(p.get("assetid") or p.get("OBJECTID")),
            end_a=a, end_b=b,
            inv_a=_elev(p.get("upelev")), inv_b=_elev(p.get("downelev")),
            diameter_m=_diameter_m(p.get("diameter")),
            roughness_n=_roughness(p.get("material"), config.default_roughness),
        ))
        for xy, tid in ((a, p.get("us_feat")), (b, p.get("ds_feat"))):
            tid = str(tid or "").strip()
            if tid:
                label_points.append((xy, tid))

    outfall_points = []
    for f in outfall_feats:
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) >= 2:
            outfall_points.append((c[0], c[1]))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, outfall_points=outfall_points, label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "penticton", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_outfall_candidates": len(outfall_points),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
