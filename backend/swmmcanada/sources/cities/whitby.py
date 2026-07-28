"""Town of Whitby storm open data -> SWMM ``NetworkIn`` (geometry topology, labelled ends).

Whitby publishes ONE storm layer (``WhitbyStormLines`` on AGOL org ``ATdLnvuMRJk8AGkQ``)
and nothing else: no node layer, no rims, no sanitary (a Durham Region asset), no
parcels/buildings. The line schema compensates: ``FR_NODE``/``TO_NODE`` ids are 99.96%
populated and typed by prefix (``ST…`` structures, ``CB…`` catch basins, ``JX…``
junctions), and every pipe carries ``DRAIN_AREA`` (ha) and ``CO_EFF`` (runoff
coefficient) — free calibration material no other scouted city publishes (kept in the
source, not yet consumed). Catch-basin SEEDS are extracted from the pipe endpoints whose
node id starts with ``CB`` — the layer encodes the inlet locations itself.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UP_INV``/``DOWN_INV`` = pipe-end INVERTS, m AMSL (~75-120 across Whitby), but only
    ~31% populated city-wide with 0 as the missing sentinel (Kitchener-style) — the
    neighbour gap-fill carries the rest. No rim source exists (default max depths).
"""
from swmmcanada.sources.cities import base

PIPES_URL = ("https://services5.arcgis.com/ATdLnvuMRJk8AGkQ/arcgis/rest/services"
             "/WhitbyStormLines/FeatureServer/0")

WHITBY_CRS = "EPSG:32617"  # UTM 17N (metric ops)
_PAGE = 2000


WhitbyClient = base.ArcGISClient


def _fetch(bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{PIPES_URL}/query", bbox, where=where, page_size=_PAGE)


def fetch_whitby_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or WhitbyClient()
    return {"mains": _fetch(bbox, client)}


def fetch_whitby_land(bbox, *, client=None) -> dict:
    """Catch-basin seeds are the pipe endpoints whose node id starts with CB — Whitby
    publishes no separate point layers at all."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or WhitbyClient()
    mains = _fetch(bbox, client)
    seeds, seen = [], set()
    for f in mains:
        p = f.get("properties") or {}
        line = (f.get("geometry") or {}).get("coordinates") or []
        if not line or len(line) < 2:
            continue
        if isinstance(line[0][0], (list, tuple)):
            line = [pt for part in line for pt in part]
        for xy, nid in ((line[0], p.get("FR_NODE")), (line[-1], p.get("TO_NODE"))):
            nid = str(nid or "")
            # Dedupe by ID *and* by snapped coordinate: several pipes reference one basin,
            # and the layer really contains re-keyed duplicates (CB24-082.4 and CB14-082.4
            # at the exact same point) — coincident tessellation seeds produce coincident
            # cells, which the overlap gate rejects.
            ckey = (round(xy[0], 6), round(xy[1], 6))
            if nid.upper().startswith("CB") and nid.upper() not in seen and ckey not in seen:
                seen.add(nid.upper())
                seen.add(ckey)
                seeds.append({"type": "Feature",
                              "properties": {"NODE_ID": nid},
                              "geometry": {"type": "Point", "coordinates": [xy[0], xy[1]]}})
    return {"catchbasins": seeds, "parcels": [], "buildings": []}


def _elev(v):
    """Invert -> float m AMSL, or None (0 = missing; Whitby sits ~75-120 m)."""
    return base.num(v, zero_missing=True)


_line_ends = base.line_ends

_WHITBY_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_whitby_network(data, *, config: base.AssembleConfig = _WHITBY_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes; FR_NODE/TO_NODE ids label the snapped nodes (dots survive
    sanitising: 'CB25-078.2' is a valid SWMM name)."""
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
        name = str(p.get("FACILITYID") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('OBJECTID')}"
        dia_mm = base.num(p.get("DIAM"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("UP_INV")), inv_b=_elev(p.get("DOWN_INV")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("PIPE_MATER"), config.default_roughness),
        ))
        for xy, tid in ((a, p.get("FR_NODE")), (b, p.get("TO_NODE"))):
            tid = str(tid or "").strip()
            if tid:
                label_points.append((xy, tid))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(pipes, label_points=label_points, config=config)
    diag = {**result.diagnostics, "city": "whitby", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom,
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
