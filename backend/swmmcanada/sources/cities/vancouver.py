"""Vancouver, BC storm-drain adapter (ADR 0020) — city #9.

TWO public sources, used for what each is good at:

* **VanMap public FeatureServer** (``maps.vancouver.ca``, access=public) carries the full
  engineering attributes the open-data portal strips: ``swGravityMain/11`` has EXPLICIT
  ``frommh``/``tomh`` manhole topology + ``diameter`` (mm) + ``slope`` (%) + ``length`` (m)
  + ``material`` + ``servstatus``; ``swManhole/12`` has ``facilityid`` + ``rimelev``.
  (Verified 2026-07-10: the portal's sewer-mains publishes ONLY effluent/material/geometry,
  while its water mains keep ``diameter_mm`` — the stripping is deliberate, not missing.)
* **Open data portal** (``opendata.vancouver.ca``, Opendatasoft, OGL-Vancouver) supplies the
  land kit: 44k+ catch basins, parcel polygons, building footprints.

Vancouver publishes NO pipe/node inverts (only manhole rims) and no outfall layer. The
vertical profile is therefore **rim-anchored** (first city with zero published inverts):
each pipe end whose manhole has a plausible ``rimelev`` gets ``invert = rim − default node
depth``; rimless ends stay None for the base assembler's neighbour backfill, and outfalls
are inferred per component. ``slope`` is real data but v1 leaves it in diagnostics —
integrating it needs multi-anchor reconciliation (ADR 0020 §3).

**Combined mains join the storm system** (author decision, 2026-07-10): downtown Vancouver
is largely combined (9.7k Combined vs 17.2k Storm mains city-wide) and combined pipes do
carry the stormwater; the sanitary tracer stays Sanitary-only so nothing is double-counted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from swmmcanada.sources.cities import base

Coord = Tuple[float, float]
VANCOUVER_CRS = "EPSG:32610"          # UTM 10N — same zone as Victoria/Surrey

# --- layers (see fixtures/vancouver/README.md) -----------------------------------
VANMAP = "https://maps.vancouver.ca/server/rest/services/Hosted"
MAINS = f"{VANMAP}/swGravityMain/FeatureServer/11"
MANHOLES = f"{VANMAP}/swManhole/FeatureServer/12"

OPEN_DATA = "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets"
CATCHBASINS = f"{OPEN_DATA}/sewer-catch-basins/exports/geojson"
PARCELS = f"{OPEN_DATA}/property-parcel-polygons/exports/geojson"
BUILDINGS = f"{OPEN_DATA}/building-footprints-2015/exports/geojson"

# ADR 0020 §2: combined mains carry the stormwater; sanitary tracer excludes them.
_STORM_WHERE = "(eflnttype = 'Storm' OR eflnttype = 'Combined') AND servstatus = 'In Service'"
_SANITARY_WHERE = "eflnttype = 'Sanitary' AND servstatus = 'In Service'"

_PAGE_SIZE, _ID_CHUNK = 1000, 80

# Vancouver terrain runs ~0–170 m AMSL; a rim outside this band is a placeholder, not an
# elevation, and must not poison the rim-anchored inverts (mirrors Calgary's rim band).
_RIM_BAND = (-10.0, 300.0)

# VanMap spells materials out in full words (the shared base map keys on codes).
_MATERIAL_N = {
    "VITRIFIED CLAY": 0.013, "CONCRETE": 0.013, "REINFORCED CONCRETE": 0.013,
    "PVC": 0.010, "HDPE": 0.011, "POLYETHYLENE": 0.011, "ASBESTOS CEMENT": 0.011,
    "DUCTILE IRON": 0.013, "CAST IRON": 0.013, "STEEL": 0.012, "BRICK": 0.015,
    "CORRUGATED METAL": 0.024,
}

VancouverMapClient = base.ArcGISClient


# --- fetch ------------------------------------------------------------------------
def _fetch_layer_bbox(layer_url: str, bbox, client, *, where: str = "1=1") -> list:
    return base.fetch_paged(client, f"{layer_url}/query", bbox, where=where, page_size=_PAGE_SIZE)


def _referenced_manhole_ids(mains) -> list:
    ids, seen = [], set()
    for feat in mains:
        props = feat.get("properties") or {}
        for key in ("frommh", "tomh"):
            mid = str(props.get(key) or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            ids.append(mid)
    return ids


def _fetch_manholes_by_id(manhole_ids, client) -> list:
    """Fetch manholes BY facilityid (chunked, quoted IN-list) rather than bbox, so a pipe
    whose far manhole sits just outside the envelope still resolves (mirrors Kitchener)."""
    ids = [m for m in manhole_ids if m]
    features, seen = [], set()
    for start in range(0, len(ids), _ID_CHUNK):
        chunk = ids[start: start + _ID_CHUNK]
        in_list = ",".join("'" + i.replace("'", "") + "'" for i in chunk)
        params = {"where": f"facilityid IN ({in_list})", "outFields": "*",
                  "returnGeometry": "true", "outSR": 4326, "f": "geojson"}
        for feat in (client.get_json(f"{MANHOLES}/query", params) or {}).get("features") or []:
            mid = (feat.get("properties") or {}).get("facilityid")
            if mid in seen:
                continue
            seen.add(mid)
            features.append(feat)
    return features


def fetch_vancouver_storm(bbox, *, client=None) -> dict:
    """Storm + combined gravity mains intersecting ``bbox`` (EPSG:4326), plus every manhole
    the mains reference (fetched by facilityid). Returns ``{"mains", "manholes"}``."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or VancouverMapClient()
    mains = _fetch_layer_bbox(MAINS, bbox, client, where=_STORM_WHERE)
    manholes = _fetch_manholes_by_id(_referenced_manhole_ids(mains), client)
    return {"mains": mains, "manholes": manholes}


def fetch_vancouver_sanitary(bbox, *, client=None) -> dict:
    """Sanitary-only gravity mains (the ADR 0011 tracer). Combined mains already live in the
    storm system (ADR 0020) and are excluded here so nothing is counted twice."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or VancouverMapClient()
    mains = _fetch_layer_bbox(MAINS, bbox, client, where=_SANITARY_WHERE)
    manholes = _fetch_manholes_by_id(_referenced_manhole_ids(mains), client)
    return {"mains": mains, "manholes": manholes}


def _fetch_opendata_bbox(url: str, bbox, client) -> list:
    """One Opendatasoft geojson export filtered to the bbox. NOTE the argument order of
    ``in_bbox``: (lat_min, lon_min, lat_max, lon_max) — verified live 2026-07-10."""
    min_lon, min_lat, max_lon, max_lat = bbox
    where = f"in_bbox(geom, {min_lat}, {min_lon}, {max_lat}, {max_lon})"
    payload = client.get_json(url, {"where": where}) or {}
    return payload.get("features") or []


def fetch_vancouver_land(bbox, *, client=None) -> dict:
    """Drainage inlets + land units from the open-data portal:
    ``{"catchbasins", "parcels", "buildings"}`` (GeoJSON Features)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or VancouverMapClient()
    return {
        "catchbasins": _fetch_opendata_bbox(CATCHBASINS, bbox, client),
        "parcels": _fetch_opendata_bbox(PARCELS, bbox, client),
        "buildings": _fetch_opendata_bbox(BUILDINGS, bbox, client),
    }


# --- network assembly ---------------------------------------------------------------
@dataclass(frozen=True)
class VancouverNetworkConfig:
    min_slope: float = 0.001
    default_max_depth_m: float = 2.0
    default_roughness: float = 0.013
    default_diameter_m: float = 0.30
    outfall_link_len_m: float = 10.0
    # ADR 0020 §3: rim-anchored vertical — invert = rimelev − this depth. 2.5 m is a
    # typical municipal manhole depth; the constant is honest-by-provenance, not surveyed.
    default_node_depth_m: float = 2.5


@dataclass(frozen=True)
class VancouverNetworkResult:
    network: "base.NetworkIn"
    diagnostics: dict = field(default_factory=dict)


def material_roughness(material: Optional[str], config: VancouverNetworkConfig) -> float:
    key = str(material or "").strip().upper()
    if key in _MATERIAL_N:
        return _MATERIAL_N[key]
    return base.material_roughness(material, config.default_roughness)


def _rim(v) -> Optional[float]:
    e = base.num(v, zero_missing=True)   # 0 is a placeholder at tidewater too — drop it
    if e is None or not (_RIM_BAND[0] <= e <= _RIM_BAND[1]):
        return None
    return e


def build_vancouver_network(
    storm: dict, *, config: VancouverNetworkConfig = VancouverNetworkConfig(),
) -> VancouverNetworkResult:
    """Assemble the Vancouver network from explicit frommh/tomh topology with rim-anchored
    inverts (ADR 0020): pipe-end invert = manhole rim − default node depth where the rim is
    plausible, else None for the base assembler's neighbour backfill."""
    mains = (storm or {}).get("mains") or []
    manhole_feats = (storm or {}).get("manholes") or []

    coords: Dict[str, Coord] = {}
    rim: Dict[str, float] = {}
    ground: List[Tuple[Coord, float]] = []
    label_points: List[Tuple[Coord, str]] = []
    for f in manhole_feats:
        p = f.get("properties") or {}
        mid = str(p.get("facilityid") or "").strip()
        xy = (f.get("geometry") or {}).get("coordinates")
        if not mid or not xy or len(xy) < 2:
            continue
        c = (xy[0], xy[1])
        coords[mid] = c
        label_points.append((c, mid))
        e = _rim(p.get("rimelev"))
        if e is not None:
            rim[mid] = e
            ground.append((c, e))

    def _end_invert(mid: str) -> Optional[float]:
        e = rim.get(mid)
        return (e - config.default_node_depth_m) if e is not None else None

    raw_pipes: List[base.RawPipe] = []
    effluent_hist: Dict[str, int] = {}
    n_rim_anchored_ends = 0
    n_dangling = 0
    n_with_slope = 0
    for m in mains:
        p = m.get("properties") or {}
        eff = str(p.get("eflnttype") or "UNK")
        effluent_hist[eff] = effluent_hist.get(eff, 0) + 1
        if base.num(p.get("slope")) is not None:
            n_with_slope += 1
        frommh = str(p.get("frommh") or "").strip()
        tomh = str(p.get("tomh") or "").strip()
        p0, p1 = base.line_ends(m.get("geometry"))
        up_xy = coords.get(frommh) or p0
        dn_xy = coords.get(tomh) or p1
        n_dangling += int(frommh not in coords) + int(tomh not in coords)
        if up_xy is None or dn_xy is None:
            continue
        inv_a, inv_b = _end_invert(frommh), _end_invert(tomh)
        n_rim_anchored_ends += int(inv_a is not None) + int(inv_b is not None)
        diameter_mm = base.num(p.get("diameter"))
        raw_pipes.append(base.RawPipe(
            name=str(p.get("facilityid") or p.get("objectid")),
            end_a=up_xy, end_b=dn_xy,
            inv_a=inv_a, inv_b=inv_b,
            diameter_m=(diameter_mm / 1000.0) if (diameter_mm and diameter_mm > 0) else None,
            roughness_n=material_roughness(p.get("material"), config),
            length_m=base.num(p.get("length")),
        ))

    result = base.assemble_network(
        raw_pipes, outfall_points=[], ground_points=ground, label_points=label_points,
        config=base.AssembleConfig(
            min_slope=config.min_slope, default_max_depth_m=config.default_max_depth_m,
            default_diameter_m=config.default_diameter_m,
            default_roughness=config.default_roughness,
            outfall_link_len_m=config.outfall_link_len_m),
    )
    diagnostics = {
        **result.diagnostics,
        "n_mains_in": len(mains), "n_manholes_in": len(manhole_feats),
        "effluent_histogram": effluent_hist,
        "n_combined_included": effluent_hist.get("Combined", 0),
        "n_rim_anchored_ends": n_rim_anchored_ends,
        "n_dangling_nodes": n_dangling,
        "n_with_slope": n_with_slope,
        "vertical_basis": f"rim minus {config.default_node_depth_m} m default node depth (ADR 0020)",
    }
    return VancouverNetworkResult(network=result.network, diagnostics=diagnostics)
