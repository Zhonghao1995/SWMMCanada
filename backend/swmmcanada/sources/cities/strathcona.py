"""Strathcona County storm/wastewater open data -> SWMM ``NetworkIn`` (geometry-inferred
topology, tier-2 Ottawa pattern).

Strathcona County (Sherwood Park; AGOL org ``B7ZrK1Hv4P1dsm9R``, hub with a licence page)
publishes one FeatureServer per dataset, all-lowercase field names. Storm_Gravity_Main
carries ``upinvert``/``downinvert`` (patchier than most of the wave — ~50% on the fixture,
verified patchy during scouting) and a ``pipetype`` vocabulary: the gravity graph takes
Collector/Transmission/Conduit/Culvert and leaves Catchbasin Leads, SPDC and Pressurized
out. No node ids. Storm_Manhole carries ``rimelev``; Storm_Discharge_Point is the outfall
layer; Storm_Catch_Basin seeds. Waste_Water_* mirrors for the ADR 0011 tracer;
Building_Footprints complete the land kit (no parcel polygons).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``upinvert``/``downinvert`` = pipe-end INVERTS, m AMSL (~700-730 Sherwood Park);
    0 = missing.
  * ``rimelev`` (manholes) = rim -> node max depths, plausibility-banded.
"""
from swmmcanada.sources.cities import base

ORG = "https://services.arcgis.com/B7ZrK1Hv4P1dsm9R/arcgis/rest/services"

STRATHCONA_CRS = "EPSG:32612"  # UTM 12N (metric ops) — first city in this zone
_PAGE = 1000

_STORM_WHERE = "pipetype IN ('Collector','Transmission','Conduit','Culvert')"


StrathconaClient = base.ArcGISClient


def _fetch(svc, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ORG}/{svc}/FeatureServer/0/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_strathcona_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or StrathconaClient()
    return {
        "mains": _fetch("Storm_Gravity_Main", bbox, client, where=_STORM_WHERE),
        "manholes": _fetch("Storm_Manhole", bbox, client),
        "outfalls": _fetch("Storm_Discharge_Point", bbox, client),
    }


def fetch_strathcona_sanitary(bbox, *, client=None) -> dict:
    """Wastewater gravity system — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or StrathconaClient()
    return {
        "mains": _fetch("Waste_Water_Gravity_Main", bbox, client),
        "manholes": _fetch("Waste_Water_Manhole", bbox, client),
    }


def fetch_strathcona_land(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or StrathconaClient()
    return {
        "catchbasins": _fetch("Storm_Catch_Basin", bbox, client),
        "parcels": [],
        "buildings": _fetch("Building_Footprints", bbox, client),
    }


def _elev(v):
    """Invert/rim -> float m AMSL, or None (0 = missing; the County sits ~700-760 m)."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): North Saskatchewan bank ~600 m to the moraine ~780 m.
_RIM_MIN, _RIM_MAX = 550.0, 850.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_line_ends = base.line_ends

_STRATHCONA_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_strathcona_network(data, *, config: base.AssembleConfig = _STRATHCONA_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes (no node ids); manhole rims; discharge-point outfalls."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    manholes = _features((data or {}).get("manholes", []) if isinstance(data, dict) else [])
    outfall_feats = _features((data or {}).get("outfalls", []) if isinstance(data, dict) else [])

    pipes = []
    n_no_geom = 0
    seen_names: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        name = str(p.get("facilityid") or p.get("objectid") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('objectid') or p.get('OBJECTID')}"
        dia_mm = base.num(p.get("diameter"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("upinvert")), inv_b=_elev(p.get("downinvert")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("material"), config.default_roughness),
        ))

    ground_points = []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _rim((f.get("properties") or {}).get("rimelev"))
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    # The Storm_Discharge_Point layer mixes true outlets with INLET-side structures
    # (the Barrie headwall disease, here inside the outfall layer itself): 11 fixture
    # pipes ran 0.06-1.7 m uphill when every point was accepted. Keep a point only when
    # it sits at the DOWNHILL end of an adjacent pipe (or the pipe publishes no inverts).
    def snap(xy):
        return (round(xy[0], config.snap_decimals), round(xy[1], config.snap_decimals))

    end_inv = {}
    for pi in pipes:
        for this_end, this_inv, other_inv in ((pi.end_a, pi.inv_a, pi.inv_b),
                                              (pi.end_b, pi.inv_b, pi.inv_a)):
            end_inv.setdefault(snap(this_end), []).append((this_inv, other_inv))

    outfall_points = []
    n_inlet_side_dropped = 0
    for f in outfall_feats:
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) < 2:
            continue
        adj = end_inv.get(snap((c[0], c[1])), [])
        informed = [(ti, oi) for ti, oi in adj if ti is not None and oi is not None]
        if informed and all(ti > oi + 1e-9 for ti, oi in informed):
            n_inlet_side_dropped += 1        # every adjacent pipe says this is the HIGH end
            continue
        outfall_points.append((c[0], c[1]))

    result = base.assemble_network(
        pipes, outfall_points=outfall_points, ground_points=ground_points, config=config,
    )
    diag = {**result.diagnostics, "city": "strathcona", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "n_inlet_side_dropped": n_inlet_side_dropped}
    return base.NetworkResult(network=result.network, diagnostics=diag)
