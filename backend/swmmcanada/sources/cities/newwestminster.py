"""City of New Westminster sewer open data -> SWMM ``NetworkIn`` (id-joined vertical,
labelled ends).

New Westminster is a COMBINED-sewer city (Ottawa/Vancouver pattern): the separated
stormwater network (2,192 mains city-wide) and the combined network (2,313) both carry the
stormwater, so both join the storm graph (ADR 0021) and the histogram counts each. The
separated sanitary system is the ADR 0011 tracer.

The vertical rides a TWO-TIER source: pipe rows publish ``UPELEV``/``DOWNELEV`` on only
~46% of storm mains (and almost never on combined ones), but the manhole layers publish
``INVERT`` (chamber flow line) and ``RIMELEV`` densely — so a pipe end whose own invert is
missing takes its FROMMH/TOMH manhole's ``INVERT`` (the Reykjavík structure-flow-line
precedent, via the id join instead of snapping), and ``RIMELEV`` feeds node max depths.
In-chamber falls survive wherever the pipe publishes its own end elevations.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UPELEV``/``DOWNELEV`` (mains) = pipe-end INVERTS, m AMSL; 0 = missing.
  * ``INVERT`` (manholes) = chamber flow line -> tier-2 stand-in for missing pipe ends.
  * ``RIMELEV`` (manholes) = rim -> node max depths, plausibility-banded.
"""
from swmmcanada.sources.cities import base

ORG = "https://services3.arcgis.com/A7O8YnTNtzRPIn7T/arcgis/rest/services"
STORM_MAINS, COMBINED_MAINS = "Sewer_Stormwater_Gravity_Main", "Sewer_Combined_Gravity_Main"
STORM_MANHOLES, COMBINED_MANHOLES = "Sewer_Stormwater_Manhole", "Sewer_Combined_Manhole"
SAN_MAINS, SAN_MANHOLES = "Sewer_Sanitary_Gravity_Main", "Sewer_Sanitary_Manhole"
INLETS, PARCELS, BUILDINGS = "Sewer_Stormwater_Inlets", "Legal_Parcel", "Building_Footprints2"

NEWWEST_CRS = "EPSG:32610"  # UTM 10N (metric ops)
_PAGE = 2000


NewWestClient = base.ArcGISClient


def _fetch(svc, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ORG}/{svc}/FeatureServer/0/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_newwestminster_storm(bbox, *, client=None) -> dict:
    """Stormwater + combined mains and manholes — combined joins storm (ADR 0021)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or NewWestClient()

    def _tag(feats, system):
        for f in feats:
            (f.setdefault("properties", {}))["_SYSTEM"] = system
        return feats

    return {
        "mains": _tag(_fetch(STORM_MAINS, bbox, client), "Storm")
                 + _tag(_fetch(COMBINED_MAINS, bbox, client), "Combined"),
        "manholes": _fetch(STORM_MANHOLES, bbox, client)
                    + _fetch(COMBINED_MANHOLES, bbox, client),
    }


def fetch_newwestminster_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary gravity system — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or NewWestClient()
    return {
        "mains": _fetch(SAN_MAINS, bbox, client),
        "manholes": _fetch(SAN_MANHOLES, bbox, client),
    }


def fetch_newwestminster_land(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or NewWestClient()
    return {
        "catchbasins": _fetch(INLETS, bbox, client),
        "parcels": _fetch(PARCELS, bbox, client),
        "buildings": _fetch(BUILDINGS, bbox, client),
    }


def _elev(v):
    """Elevation -> float m AMSL, or None (0 = missing; the Fraser bank sits >= ~1 m)."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): Fraser shore ~2 m to the Massey Heights crest ~120 m.
_RIM_MIN, _RIM_MAX = 0.5, 200.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_line_ends = base.line_ends

_NEWWEST_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_newwestminster_network(data, *, config: base.AssembleConfig = _NEWWEST_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes; a pipe end without its own invert takes its FROMMH/TOMH manhole's
    chamber INVERT (tier 2); manhole RIMELEV feeds max depths; FROMMH/TOMH label nodes."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    manholes = _features((data or {}).get("manholes", []) if isinstance(data, dict) else [])

    mh_invert, mh_rim = {}, {}
    ground_points = []
    for f in manholes:
        p = f.get("properties") or {}
        fid = str(p.get("FACILITYID") or "").strip()
        c = (f.get("geometry") or {}).get("coordinates") or []
        inv, rim = _elev(p.get("INVERT")), _rim(p.get("RIMELEV"))
        if fid:
            if inv is not None:
                mh_invert[fid] = inv
            if rim is not None:
                mh_rim[fid] = rim
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    pipes, label_points = [], []
    n_no_geom = n_mh_lifted_ends = 0
    system_hist: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        system_hist[p.get("_SYSTEM", "Storm")] = system_hist.get(p.get("_SYSTEM", "Storm"), 0) + 1
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        frommh, tomh = str(p.get("FROMMH") or "").strip(), str(p.get("TOMH") or "").strip()
        inv_a, inv_b = _elev(p.get("UPELEV")), _elev(p.get("DOWNELEV"))
        if inv_a is None and frommh in mh_invert:
            inv_a = mh_invert[frommh]
            n_mh_lifted_ends += 1
        if inv_b is None and tomh in mh_invert:
            inv_b = mh_invert[tomh]
            n_mh_lifted_ends += 1
        dia_mm = base.num(p.get("DIAMETER"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=str(p.get("JDE_FEATURE_ID") or p.get("OBJECTID")),
            end_a=a, end_b=b, inv_a=inv_a, inv_b=inv_b,
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("MATERIAL"), config.default_roughness),
        ))
        for xy, tid in ((a, frommh), (b, tomh)):
            if tid:
                label_points.append((xy, f"MH{tid}"))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, ground_points=ground_points, label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "newwestminster", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "n_mh_lifted_ends": n_mh_lifted_ends, "system_histogram": system_hist,
            "n_combined_included": system_hist.get("Combined", 0),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
