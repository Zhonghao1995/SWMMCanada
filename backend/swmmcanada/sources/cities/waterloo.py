"""City of Waterloo stormwater open data -> SWMM ``NetworkIn`` (geometry topology, labelled ends).

The City of Waterloo publishes its own network on its own AGOL org (``ZpeBVw5o1kjit7LT``,
data.waterloo.ca) — it is NOT in the City of Kitchener feed next door, whose ``OWNERSHIP =
WATERLOO`` rows are 38 border pipes. ``Stormwater_Gravity_Mains`` carries per-end inverts,
section dims/shape and material; ``Stormwater_Manholes`` carries rims and the city's own
structure ids; dedicated outlet and catch-basin layers, a parcel fabric and building
rooftops complete the ADR 0005 parcel/building kit. Licence: Waterloo Open Data User
Licence (OGL family: copy / adapt / commercial use, no endorsement implied).

Only ``ASSET_TYPE = 'Collector'`` joins the gravity graph (9,890 of 16,672 lines, 87% with
both inverts). Catchbasin Leads (6,370, 15% inverts) stay out like every other city's leads
— catch basins seed subcatchments instead — and Culverts/Ditches/Track+Trench Drains are
open-channel or site structures, not the piped minor system.

Topology is assembled from polyline endpoints (Coquitlam pattern). Verified live
2026-09-18 on 1,845 collectors: ``line[0]`` is the FROM (upstream) end in 698/698 id-joined
pipes and endpoints coincide with their manholes (median 0.00 m, p99 < 4 m).
``FROM_ASSET_ID``/``TO_ASSET_ID`` are null on ~20% of collectors and resolving the rest by
id merged only 6 of 157 components, so geometry is the authority and manhole ``ENTNAME``
(e.g. ``L53R-5``) labels the nodes.

Elevation semantics (per the #157 convention — verified live 2026-09-18):
  * ``UPSTREAMINVERT``/``DOWNSTREAMINVERT`` = pipe-end INVERTS, metres AMSL. TWO missing
    sentinels: a literal ``0`` (2,926 storm lines) and ``111.111`` (2,734 — the only value
    anywhere between 0 and 250). Real inverts run 304.7-~405 m, so a plausibility band
    screens both, plus the handful of junk values above the city's highest ground.
  * ``TOPOFMH`` (manholes) = cover/rim -> node max depths; same sentinels, same band
    (real rims 309.5-407.4 m).
  * ``INVERTELEVATION`` (manholes) is the chamber invert; deliberately NOT read — node
    invert stays the lowest connected pipe-end invert (see ASSUMPTIONS.md).
"""
from swmmcanada.sources.cities import base

ORG = "https://services.arcgis.com/ZpeBVw5o1kjit7LT/arcgis/rest/services"
STORM_MAINS = f"{ORG}/Stormwater_Gravity_Mains/FeatureServer/0"
STORM_MANHOLES = f"{ORG}/Stormwater_Manholes/FeatureServer/0"
STORM_OUTLETS = f"{ORG}/Stormwater_Outlets/FeatureServer/0"
STORM_CATCHBASINS = f"{ORG}/Stormwater_Catch_Basins/FeatureServer/0"
SAN_MAINS = f"{ORG}/Sanitary_Sewer_Gravity_Mains/FeatureServer/0"
PARCELS = f"{ORG}/Property_Fabric/FeatureServer/1358"   # the service's only layer id
BUILDINGS = f"{ORG}/Buildings/FeatureServer/0"

WATERLOO_CRS = "EPSG:32617"   # UTM 17N (metric ops) — same zone as Kitchener/London

# Municipal boundary (lon, lat ring): the city's own ``CityBoundary`` layer, simplified at
# 25 m in UTM (774 -> 71 vertices, max deviation 31 m; recorded 2026-09-18). Kitchener and
# Waterloo meet along a diagonal border, so their coverage boxes overlap by kilometres —
# Uptown Waterloo sits inside Kitchener's box and downtown Kitchener inside Waterloo's. The
# registry dispatches on this polygon instead of the box.
WATERLOO_BOUNDARY = (
    (-80.48065, 43.53121), (-80.47919, 43.52775), (-80.47679, 43.52543), (-80.47063, 43.52293),
    (-80.46824, 43.5212), (-80.46744, 43.5199), (-80.46757, 43.51861), (-80.46898, 43.51702),
    (-80.47173, 43.51621), (-80.4835, 43.51612), (-80.48516, 43.51537), (-80.48741, 43.51323),
    (-80.49332, 43.51251), (-80.49503, 43.51146), (-80.49489, 43.50787), (-80.49317, 43.50346),
    (-80.49191, 43.50217), (-80.49064, 43.50178), (-80.48898, 43.5018), (-80.48087, 43.50352),
    (-80.47956, 43.50428), (-80.47752, 43.50095), (-80.48097, 43.50011), (-80.48144, 43.49875),
    (-80.48136, 43.49848), (-80.48107, 43.49889), (-80.4808, 43.48965), (-80.48416, 43.48529),
    (-80.4874, 43.48402), (-80.48775, 43.48427), (-80.48803, 43.48406), (-80.48717, 43.48345),
    (-80.48818, 43.48267), (-80.48652, 43.48149), (-80.49187, 43.48125), (-80.49428, 43.48043),
    (-80.49606, 43.48111), (-80.49782, 43.48051), (-80.49579, 43.4768), (-80.49501, 43.47634),
    (-80.49236, 43.47598), (-80.49145, 43.4745), (-80.49665, 43.47281), (-80.49531, 43.47159),
    (-80.49637, 43.47132), (-80.49552, 43.47054), (-80.50093, 43.46864), (-80.49845, 43.46635),
    (-80.50171, 43.46464), (-80.50062, 43.46363), (-80.50113, 43.46345), (-80.50081, 43.46316),
    (-80.51227, 43.45885), (-80.5127, 43.45899), (-80.5202, 43.45633), (-80.52206, 43.45484),
    (-80.52723, 43.45381), (-80.52654, 43.45308), (-80.53384, 43.45048), (-80.53432, 43.45138),
    (-80.541, 43.44901), (-80.53864, 43.44689), (-80.54242, 43.44555), (-80.54094, 43.44364),
    (-80.54283, 43.44307), (-80.5449, 43.44412), (-80.54602, 43.44203), (-80.54722, 43.44115),
    (-80.57346, 43.43227), (-80.62598, 43.47969), (-80.48065, 43.53121),
)
_PAGE = 1000                  # layer maxRecordCount (the parcel fabric allows 2000)

_COLLECTOR_WHERE = "ASSET_TYPE = 'Collector'"
# The fabric tags the street right-of-way and rail corridors as parcels of their own; as
# "lots" they would hand a whole road to one catch basin. Left out, that land returns to
# the street-sliver path (AOI minus parcels). NULL-typed rows (a handful) stay in.
_LOT_WHERE = "PARCELTYPE IS NULL OR PARCELTYPE NOT IN ('ROAD PARCEL', 'RAILWAY PARCEL')"


WaterlooClient = base.ArcGISClient


def _fetch(layer_url, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{layer_url}/query", bbox, where=where, page_size=_PAGE)


def fetch_waterloo_storm(bbox, *, client=None) -> dict:
    """Collector mains + manholes (rims, structure ids) + the dedicated outlet layer."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or WaterlooClient()
    return {
        "mains": _fetch(STORM_MAINS, bbox, client, where=_COLLECTOR_WHERE),
        "manholes": _fetch(STORM_MANHOLES, bbox, client),
        "outfalls": _fetch(STORM_OUTLETS, bbox, client),
    }


def fetch_waterloo_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary gravity mains — the second tagged system (ADR 0011). The layer is
    gravity-only (force mains live on their own service) and all ``Active``. Sanitary
    manholes are not published openly, so no rims: depths take the assembler's defaults."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or WaterlooClient()
    return {"mains": _fetch(SAN_MAINS, bbox, client)}


def fetch_waterloo_land(bbox, *, client=None) -> dict:
    """Catch basins + lots + building rooftops for the ADR 0005 subcatchment method."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or WaterlooClient()
    return {
        "catchbasins": _fetch(STORM_CATCHBASINS, bbox, client),
        "parcels": _fetch(PARCELS, bbox, client, where=_LOT_WHERE),
        "buildings": _fetch(BUILDINGS, bbox, client),
    }


# Plausible elevation band (m AMSL). Waterloo's ground runs ~305 m (Grand River side) to
# ~410 m (west moraine); both missing sentinels (0 and 111.111) and the stray junk above
# fall outside it.
_ELEV_MIN, _ELEV_MAX = 290.0, 420.0


def _elev(v):
    """Invert/rim -> float m AMSL, or None when missing or outside the plausible band."""
    f = base.num(v)
    return f if (f is not None and _ELEV_MIN <= f <= _ELEV_MAX) else None


# Waterloo publishes full words (one misspelt at source), the shared table wants codes.
_MATERIAL_ALIASES = {
    "CONCRETE": "CONC", "POLYVINYL CHLORIDE": "PVC", "CORRUGATED STEEL PIPE": "CSP",
    "ASBESTOS CEMENT": "AC", "VITIFIED CLAY": "VC", "VITRIFIED CLAY": "VC",
    "CAST IRON": "CI", "DUCTILE IRON": "DI", "HIGH DENSITY POLYETHYLENE": "HDPE",
    "POLYETHYLENE": "PE", "BRICK": "BR", "STEEL": "STL",
}


def _roughness(material, default):
    code = str(material or "").strip().upper()
    return base.material_roughness(_MATERIAL_ALIASES.get(code, code), default)


# Endpoints coincide with their structures; ~1 m snapping (snap_decimals=5) matches the
# other geometry-assembled adapters and absorbs the few-metre tail.
_WATERLOO_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def _mm(v):
    f = base.num(v, zero_missing=True)
    return (f / 1000.0) if (f is not None and f > 0) else None


def build_waterloo_network(data, *, config: base.AssembleConfig = _WATERLOO_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes from mains (storm or sanitary schema — field names auto-detected),
    node labels + rims from manholes, outfalls from the outlet layer. ``line[0]`` is the
    upstream end, so ``UPSTREAMINVERT`` belongs to ``end_a``."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    manholes = _features((data or {}).get("manholes", []) if isinstance(data, dict) else [])
    outfall_feats = _features((data or {}).get("outfalls", []) if isinstance(data, dict) else [])

    pipes = []
    n_no_geom = n_inv_screened = 0
    for f in mains:
        p = f.get("properties") or {}
        a, b = base.line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        inv_a, inv_b = _elev(p.get("UPSTREAMINVERT")), _elev(p.get("DOWNSTREAMINVERT"))
        n_inv_screened += sum(1 for raw, kept in ((p.get("UPSTREAMINVERT"), inv_a),
                                                  (p.get("DOWNSTREAMINVERT"), inv_b))
                              if raw is not None and kept is None)
        # storm mains: HEIGHT/WIDTH (mm, circular when equal); sanitary mains: DIAMETER (mm)
        height_m, width_m = _mm(p.get("HEIGHT")), _mm(p.get("WIDTH"))
        dims = [d for d in (height_m, width_m, _mm(p.get("DIAMETER"))) if d]
        pipes.append(base.RawPipe(
            name=str(p.get("ASSET_ID") or p.get("OBJECTID")),
            end_a=a, end_b=b, inv_a=inv_a, inv_b=inv_b,
            diameter_m=max(dims) if dims else None,   # equivalent circular: never understated
            roughness_n=_roughness(p.get("MATERIAL"), config.default_roughness),
            length_m=base.num(p.get("LENGTH_M"), zero_missing=True),
            shape=p.get("CROSSSECTIONSHAPE"), height_m=height_m, width_m=width_m,
        ))

    ground_points, label_points = [], []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) < 2:
            continue
        p = f.get("properties") or {}
        rim = _elev(p.get("TOPOFMH"))
        if rim is not None:
            ground_points.append(((c[0], c[1]), rim))
        name = str(p.get("ENTNAME") or "").strip()
        if name:
            label_points.append(((c[0], c[1]), name))

    outfall_points = []
    for f in outfall_feats:
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) >= 2:
            outfall_points.append((c[0], c[1]))

    # ENTNAME is a human-keyed field: the shared label safety drops ids that would collide
    # (duplicates across structures, SWMM's case-folding, the reserved N# namespace).
    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, outfall_points=outfall_points, ground_points=ground_points,
        label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "waterloo", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "n_inverts_screened": n_inv_screened,
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
