"""City of Kingston storm data -> SWMM ``NetworkIn`` (geometry topology, labelled ends).

Kingston's open data rides utility.arcgis.com **proxy** URLs (one hash per service — they
look token-gated but answer anonymously; each URL comes from the city's official DCAT
catalogue). Storm Pipe has the richest link schema of the wave: three node-id families
(``UPSTREAM/DOWNSTREAM_MANHOLE_ID`` MHS-…, ``…_INLET_ID`` CB-…, ``DOWNSTREAM_OUTLET_ID``)
plus ``SLOPE`` and ``LENGTH_M``. ``CONSTRUCTION_STATUS='Constructed'`` keeps
Removed/Retired/Approved-for-Construction rows out. The Storm Manhole layer publishes NO
elevations (ids only), so node max depths keep the assembler default (Ottawa-style).
Land: Storm Inlet points seed subcatchments and the Buildings proxy serves footprints;
the Parcel MapServer does not answer anonymous spatial queries, so parcels stay empty.
Kingston publishes no sanitary network (``sanitary=None`` — first wave-2 city without the
ADR 0011 tracer).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UPSTREAM_INVERT``/``DOWNSTREAM_INVERT`` = pipe-end INVERTS, m AMSL — inside a
    plausibility band: city-wide the values are cleanly bimodal (6,018 real rows at
    60-200 m vs 467 placeholder rows at <=2 m against a Lake Ontario shore of ~74.5 m,
    plus 5 junk rows between), so anything outside (60, 200) is missing. The band lives
    in ``_INVERT_MIN/_INVERT_MAX``, same pattern as Regina's.
  * ``DROP_INVERT_ELEVATION_M`` (drop-structure invert) is not read — drops survive as
    conduit offsets from the per-end inverts (#130).
"""
from swmmcanada.sources.cities import base

# Official DCAT-published proxy endpoints (opendatakingston.cityofkingston.ca).
PIPES_URL = ("https://utility.arcgis.com/usrsvcs/servers/bf4f2517b3514cbbb8df5591361cc829"
             "/rest/services/Eng/Storm_Pipe/FeatureServer/0")
INLETS_URL = ("https://utility.arcgis.com/usrsvcs/servers/086384dba1804bcbbb5c503d9a777cd1"
              "/rest/services/Eng/Storm_Inlet/FeatureServer/0")
BUILDINGS_URL = ("https://utility.arcgis.com/usrsvcs/servers/4c2e77dc07614be39db165eeedcad584"
                 "/rest/services/Buildings/FeatureServer/1")

KINGSTON_CRS = "EPSG:32618"  # UTM 18N (metric ops) — same zone as Ottawa
_PAGE = 2000                 # server maxRecordCount is 5000; 2000 keeps pages uniform

_CONSTRUCTED_WHERE = "CONSTRUCTION_STATUS='Constructed'"


KingstonClient = base.ArcGISClient


def _fetch(url, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{url}/query", bbox, where=where, page_size=_PAGE)


def fetch_kingston_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KingstonClient()
    return {"mains": _fetch(PIPES_URL, bbox, client, where=_CONSTRUCTED_WHERE)}


def fetch_kingston_land(bbox, *, client=None) -> dict:
    """Storm inlets as seeds + building footprints; parcels unavailable anonymously."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KingstonClient()
    return {
        "catchbasins": _fetch(INLETS_URL, bbox, client),
        "parcels": [],
        "buildings": _fetch(BUILDINGS_URL, bbox, client),
    }


# Kingston's inverts are cleanly bimodal city-wide: real values 60-200 m AMSL, placeholder
# rows at 0/1/<=2 m (Lake Ontario sits at ~74.5 m, so nothing real can be lower than ~60).
_INVERT_MIN, _INVERT_MAX = 60.0, 200.0


def _invert(v):
    f = base.num(v)
    return f if (f is not None and _INVERT_MIN <= f <= _INVERT_MAX) else None


_line_ends = base.line_ends

_KINGSTON_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_kingston_network(data, *, config: base.AssembleConfig = _KINGSTON_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes from Storm Pipe; endpoint labels prefer manhole ids, then inlet /
    outlet ids; a DN endpoint carrying DOWNSTREAM_OUTLET_ID is an outfall candidate."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)

    pipes, label_points, outfall_points = [], [], []
    n_no_geom = 0
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        dia_mm = base.num(p.get("DIAMETER_MM"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=str(p.get("STORM_SEWER_ID") or p.get("OBJECTID")),
            end_a=a, end_b=b,
            inv_a=_invert(p.get("UPSTREAM_INVERT")), inv_b=_invert(p.get("DOWNSTREAM_INVERT")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(
                str(p.get("PIPE_MATERIAL") or "").replace("Reinf. ", ""), config.default_roughness),
            length_m=base.num(p.get("LENGTH_M"), zero_missing=True),
        ))
        up_id = p.get("UPSTREAM_MANHOLE_ID") or p.get("UPSTREAM_INLET_ID")
        dn_id = (p.get("DOWNSTREAM_MANHOLE_ID") or p.get("DOWNSTREAM_OUTLET_ID")
                 or p.get("DOWNSTREAM_INLET_ID"))
        for xy, tid in ((a, up_id), (b, dn_id)):
            tid = str(tid or "").strip()
            if tid:
                label_points.append((xy, tid))
        if p.get("DOWNSTREAM_OUTLET_ID"):
            outfall_points.append(b)

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, outfall_points=outfall_points, label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "kingston", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_outfall_candidates": len(outfall_points),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
