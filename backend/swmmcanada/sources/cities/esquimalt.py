"""Township of Esquimalt drain/sewer open data -> SWMM ``NetworkIn`` (id-joined
directional inverts — the wave's most unusual vertical).

Esquimalt (gis.esquimalt.ca, on-prem ArcGIS Server) publishes Drain Mains (Services/Drain
layer 4) with ``UPSTREAM_MANHOLE``/``DOWNSTREAM_MANHOLE`` ids but NO pipe elevations at
all; the elevations live on Drain Manholes (2) BY COMPASS DIRECTION: ``NORTH_INVERT`` /
``SOUTH_INVERT`` / ``EAST_INVERT`` / ``WEST_INVERT`` (per chamber wall) plus
``CENTER_INVERT`` and ``RIM_ELEVATION``. A pipe end joined to its manhole (via the
manhole layer's ``ID`` = ``DMH…``) takes the compass invert matching the pipe's BEARING
out of the chamber (±45°), falling back to CENTER, then to the lowest published wall.
``END``/``OUT`` sentinels in the id columns mean a dead end / outfall (no join). The
sanitary system is simpler — Sewer Mains (Services/Sewer layer 5) carry
``UPSTREAM/DOWNSTREAM_ELEVATION`` on the pipe rows directly. Land kit: Drain Catch Basin
(0) seeds + Cadastre Parcel (0) + Buildings_EOC (0). The Outfalls service marks shoreline
discharge points.

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * Drain manhole ``N/S/E/W_INVERT`` = the flow-line elevation at that chamber wall ->
    lifted onto the joined pipe end by bearing; ``CENTER_INVERT`` = chamber channel.
  * ``RIM_ELEVATION`` (alias "GROUND ELEVATION") = rim -> node max depths, banded.
  * Sanitary ``UPSTREAM/DOWNSTREAM_ELEVATION`` = pipe-end INVERTS (on-pipe, 92% here).
"""
import math

from swmmcanada.sources.cities import base

ARC = "https://gis.esquimalt.ca/arcgis/rest/services/Services"
DRAIN_CATCHBASINS, DRAIN_MANHOLES, DRAIN_MAINS = 0, 2, 4
SEWER_MANHOLES, SEWER_MAINS = 4, 5

ESQUIMALT_CRS = "EPSG:32610"  # UTM 10N — same zone as Victoria next door
_PAGE = 1000


EsquimaltClient = base.ArcGISClient


def _strip_z(feat: dict) -> dict:
    """Esquimalt's layers are Z-enabled and the Z is often literal ``null`` —
    ``[x, y, null]`` crashes shapely. Truncate every coordinate to (x, y)."""
    def trunc(c):
        if isinstance(c, (list, tuple)):
            if c and isinstance(c[0], (int, float)):
                return list(c[:2])
            return [trunc(x) for x in c]
        return c
    g = feat.get("geometry")
    if g and "coordinates" in g:
        g["coordinates"] = trunc(g["coordinates"])
    return feat


def _fetch(svc, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ARC}/{svc}/MapServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE, transform=_strip_z)


def fetch_esquimalt_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or EsquimaltClient()
    return {
        "mains": _fetch("Drain", DRAIN_MAINS, bbox, client),
        "manholes": _fetch("Drain", DRAIN_MANHOLES, bbox, client),
        "outfalls": _fetch("Outfalls", 0, bbox, client),
    }


def fetch_esquimalt_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary mains (per-end elevations on the rows) — ADR 0011 tracer."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or EsquimaltClient()
    return {"mains": _fetch("Sewer", SEWER_MAINS, bbox, client)}


def fetch_esquimalt_land(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or EsquimaltClient()
    return {
        "catchbasins": _fetch("Drain", DRAIN_CATCHBASINS, bbox, client),
        "parcels": _fetch("Cadastre", 0, bbox, client),
        "buildings": _fetch("Buildings_EOC", 0, bbox, client),
    }


def _elev(v):
    """Elevation -> float m AMSL, or None (0 = missing; the harbour shore sits >= ~1 m)."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): shoreline ~1 m to Highrock summit ~70 m.
_RIM_MIN, _RIM_MAX = 0.5, 120.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


def pick_directional_invert(mh_props, bearing_deg):
    """The chamber-wall invert matching a pipe's bearing OUT of the manhole (±45° per
    compass quadrant), falling back to CENTER_INVERT, then to the lowest published wall.

    ``bearing_deg`` is degrees clockwise from north. Returns None when the manhole
    publishes nothing at all."""
    walls = {
        "NORTH_INVERT": 0.0, "EAST_INVERT": 90.0, "SOUTH_INVERT": 180.0, "WEST_INVERT": 270.0,
    }
    if bearing_deg is not None:
        b = bearing_deg % 360.0
        for field, centre in walls.items():
            diff = abs((b - centre + 180.0) % 360.0 - 180.0)
            if diff <= 45.0:
                v = _elev(mh_props.get(field))
                if v is not None:
                    return v
                break
    v = _elev(mh_props.get("CENTER_INVERT"))
    if v is not None:
        return v
    published = [x for x in (_elev(mh_props.get(f)) for f in walls) if x is not None]
    return min(published) if published else None


def _bearing(from_xy, to_xy):
    """Degrees clockwise from north, from one lon/lat to another (local flat approx)."""
    dx = (to_xy[0] - from_xy[0]) * math.cos(math.radians((from_xy[1] + to_xy[1]) / 2))
    dy = to_xy[1] - from_xy[1]
    if dx == 0 and dy == 0:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360.0


_line_ends = base.line_ends

_ESQUIMALT_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_esquimalt_network(data, *, config: base.AssembleConfig = _ESQUIMALT_ASSEMBLE) -> base.NetworkResult:
    """Storm: pipes take directional manhole inverts via the DMH id join; sanitary: the
    rows carry their own UPSTREAM/DOWNSTREAM_ELEVATION (schema auto-detected)."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    manholes = _features((data or {}).get("manholes", []) if isinstance(data, dict) else [])
    outfall_feats = _features((data or {}).get("outfalls", []) if isinstance(data, dict) else [])

    mh_by_id, ground_points = {}, []
    for f in manholes:
        p = f.get("properties") or {}
        mid = str(p.get("ID") or "").strip()
        c = (f.get("geometry") or {}).get("coordinates") or []
        if mid:
            mh_by_id[mid] = p
        rim = _rim(p.get("RIM_ELEVATION"))
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    pipes, label_points = [], []
    n_no_geom = n_directional_ends = 0
    seen_names: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        # sanitary rows publish their own per-end elevations; storm rows join manholes
        if "UPSTREAM_ELEVATION" in p:
            inv_a, inv_b = _elev(p.get("UPSTREAM_ELEVATION")), _elev(p.get("DOWNSTREAM_ELEVATION"))
            up_id, dn_id = p.get("UPSTREAM_SMH"), p.get("DOWNSTREAM_SMH")
        else:
            up_id, dn_id = p.get("UPSTREAM_MANHOLE"), p.get("DOWNSTREAM_MANHOLE")
            inv_a = inv_b = None
            up_mh = mh_by_id.get(str(up_id or "").strip())
            dn_mh = mh_by_id.get(str(dn_id or "").strip())
            if up_mh is not None:
                inv_a = pick_directional_invert(up_mh, _bearing(a, b))
                n_directional_ends += int(inv_a is not None)
            if dn_mh is not None:
                inv_b = pick_directional_invert(dn_mh, _bearing(b, a))
                n_directional_ends += int(inv_b is not None)
        name = str(p.get("ID") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('OBJECTID')}"
        dia_mm = base.num(p.get("DIAMETER"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b, inv_a=inv_a, inv_b=inv_b,
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("MATERIAL"), config.default_roughness),
            length_m=base.num(p.get("LENGTH"), zero_missing=True),
        ))
        for xy, tid in ((a, up_id), (b, dn_id)):
            tid = str(tid or "").strip()
            if tid and tid.upper() not in ("END", "OUT", "N/A"):
                label_points.append((xy, tid))

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
    diag = {**result.diagnostics, "city": "esquimalt", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "n_directional_invert_ends": n_directional_ends,
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
