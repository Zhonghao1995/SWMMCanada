"""City of Barrie storm open data -> SWMM ``NetworkIn`` (geometry topology, labelled ends).

Barrie serves a clean ``Open_Data`` folder on its own ArcGIS Server (gispublic.barrie.ca).
``StormInfrastructure`` has two layers that matter: ``Storm Linear`` (1) mixes piped and
open-channel assets, so the gravity graph filters ``TYPE IN ('LOCAL','TRUNK','CULVERT',
'ENTRANCE CULVERT') AND STATUS='ACTIVE'`` — WATERCOURSE/DITCH/SWALE rows carry null inverts
by design and stay out; ``Storm Device`` (0) is one point layer wearing three hats: rims
(``TOPELEV``) for node max depths, ``TYPE='OUTFALL'``/outlet-family points as outfall
candidates (headwalls excluded — see ``_OUTFALL_TYPES``), and the catch-basin family as
subcatchment seeds. ``SanitaryInfrastructure``
mirrors the exact same schema (``TYPE IN ('LOCAL','TRUNK')`` keeps FORCE mains out), so one
builder serves both tagged systems (ADR 0011). Parcels (``ParcelPublishing/2``) and
Buildings (``FacilitiesStreets/36``) feed the ADR 0005 subcatchment method.

Pipes carry real non-circular sections: ``PIPESHP`` (CIRCULAR/ARCH/CLOSED_RECT/...) with
``WIDTH``/``HEIGHT`` in mm rides the #130 shape mapping. ``FROM_ID``/``TO_ID`` label the
snapped endpoint nodes with real asset ids (topology itself is geometry-assembled).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``INV_UP_ELV``/``INV_DN_ELV`` (storm + sanitary linear) = pipe-end INVERTS, m AMSL
    (~180-330 m across Barrie); 0/null = missing (open-channel rows are null by design).
  * ``TOPELEV`` (Storm/Sanitary Device) = top/rim elevation -> node max depths, screened by
    a plausibility band; ``DEPTH`` (device) is not read (node invert stays min pipe end,
    see ASSUMPTIONS.md).
"""
from swmmcanada.sources.cities import base

ARC = "https://gispublic.barrie.ca/arcgis/rest/services/Open_Data"
STORM_SVC, SAN_SVC = "StormInfrastructure", "SanitaryInfrastructure"
STORM_DEVICES, STORM_LINEAR = 0, 1
SAN_DEVICES, SAN_LINEAR = 1, 2
PARCELS_SVC, PARCELS = "ParcelPublishing", 2
BUILDINGS_SVC, BUILDINGS = "FacilitiesStreets", 36

BARRIE_CRS = "EPSG:32617"  # UTM 17N (metric ops) — same zone as Kitchener/London
_PAGE = 1000               # layer maxRecordCount

# Piped gravity graph only: open-channel TYPEs (WATERCOURSE/DITCH/SWALE/ENGINEERED CHANNEL)
# are not part of it (their inverts are null by design). Culverts convey and stay in,
# matching Regina's culvert call.
_STORM_WHERE = "TYPE IN ('LOCAL','TRUNK','CULVERT','ENTRANCE CULVERT') AND STATUS='ACTIVE'"
_SAN_WHERE = "TYPE IN ('LOCAL','TRUNK') AND STATUS='ACTIVE'"   # FORCE mains stay out

# Storm Device TYPE families (observed vocabulary, 2026-07-28). HEADWALL is deliberately
# NOT an outfall candidate: headwalls stand at BOTH ends of a culvert, and a single-link
# inlet headwall accepted as an outfall forces flow uphill into it (caught by the fixture:
# one culvert re-oriented INTO its 1.2 m-higher inlet). Components that lose their only
# candidate this way drain via the assembler's per-component sinks instead.
_OUTFALL_TYPES = {"OUTFALL", "OUTLET STRUCTURE", "SWMF OUTLET", "SWMF-OUTLET"}
_CATCHBASIN_TYPES = {"CATCH BASIN", "SUPER CATCH BASIN", "REAR LOT CATCH BASIN",
                     "DCB", "CBMH", "DCBMH", "DICB", "DICBMH", "RLCBMH", "DITCH INLET"}


BarrieClient = base.ArcGISClient


def _fetch(service, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ARC}/{service}/MapServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_barrie_storm(bbox, *, client=None) -> dict:
    """Active piped storm linears + the Storm Device point layer (rims/outfalls)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or BarrieClient()
    return {
        "mains": _fetch(STORM_SVC, STORM_LINEAR, bbox, client, where=_STORM_WHERE),
        "devices": _fetch(STORM_SVC, STORM_DEVICES, bbox, client),
    }


def fetch_barrie_sanitary(bbox, *, client=None) -> dict:
    """Active gravity sanitary linears + devices — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or BarrieClient()
    return {
        "mains": _fetch(SAN_SVC, SAN_LINEAR, bbox, client, where=_SAN_WHERE),
        "devices": _fetch(SAN_SVC, SAN_DEVICES, bbox, client),
    }


def fetch_barrie_land(bbox, *, client=None) -> dict:
    """Catch-basin family devices + parcels + buildings for the ADR 0005 method."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or BarrieClient()
    devices = _fetch(STORM_SVC, STORM_DEVICES, bbox, client)
    catchbasins = [f for f in devices
                   if str((f.get("properties") or {}).get("TYPE") or "").strip().upper()
                   in _CATCHBASIN_TYPES]
    return {
        "catchbasins": catchbasins,
        "parcels": _fetch(PARCELS_SVC, PARCELS, bbox, client),
        "buildings": _fetch(BUILDINGS_SVC, BUILDINGS, bbox, client),
    }


def _elev(v):
    """Invert/rim -> float m AMSL, or None when missing (0 sentinel: Barrie sits ~180-330 m)."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): Kempenfelt Bay shore ~219 m up to ~330 m.
_RIM_MIN, _RIM_MAX = 150.0, 400.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_MATERIAL_ALIASES = {
    "CONCRETE": "CONC", "REINFORCED CONCRETE": "CONC", "CORRUGATED STEEL": "CSP",
    "CORRUGATED METAL": "CMP", "ASBESTOS CEMENT": "AC", "DUCTILE IRON": "DI",
    "CAST IRON": "CI", "VITRIFIED CLAY": "VC", "POLYETHYLENE": "PE",
}


def _roughness(material, default):
    code = str(material or "").strip().upper()
    return base.material_roughness(_MATERIAL_ALIASES.get(code, code), default)


_line_ends = base.line_ends

_BARRIE_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_barrie_network(data, *, config: base.AssembleConfig = _BARRIE_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes from the linear layer (storm or sanitary — identical schema), node
    labels from FROM/TO_ID, rims + outfall candidates from the device layer."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    devices = _features((data or {}).get("devices", []) if isinstance(data, dict) else [])

    pipes, label_points = [], []
    n_no_geom = 0
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        w_mm = base.num(p.get("WIDTH"), zero_missing=True)
        h_mm = base.num(p.get("HEIGHT"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=str(p.get("ASSETID") or p.get("OBJECTID")),
            end_a=a, end_b=b,
            inv_a=_elev(p.get("INV_UP_ELV")), inv_b=_elev(p.get("INV_DN_ELV")),
            diameter_m=(w_mm / 1000.0) if w_mm else None,
            roughness_n=_roughness(p.get("MATERIAL"), config.default_roughness),
            shape=p.get("PIPESHP"),
            height_m=(h_mm / 1000.0) if h_mm else None,
            width_m=(w_mm / 1000.0) if w_mm else None,
        ))
        for xy, tid in ((a, p.get("FROM_ID")), (b, p.get("TO_ID"))):
            if tid:
                label_points.append((xy, str(tid)))

    ground_points, outfall_points = [], []
    for f in devices:
        p = f.get("properties") or {}
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) < 2:
            continue
        rim = _rim(p.get("TOPELEV"))
        if rim is not None:
            ground_points.append(((c[0], c[1]), rim))
        if str(p.get("TYPE") or "").strip().upper() in _OUTFALL_TYPES:
            outfall_points.append((c[0], c[1]))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, outfall_points=outfall_points, ground_points=ground_points,
        label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "barrie", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "n_outfall_candidates": len(outfall_points),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
