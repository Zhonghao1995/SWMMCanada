"""City of Windsor sewer open data -> SWMM ``NetworkIn`` — the wave's first
DOWNLOAD-AND-CACHE city (no query API exists).

Windsor (opendata.citywindsor.ca) publishes static ZIP shapefile dumps under predictable
``/Uploads/<name>.zip`` paths (the download links are JS-rendered, the files are not).
``Sewer Pipeline.zip`` carries the whole 26k-pipe network in EPSG:26917 with DBF-truncated
column names: ``Upstream_E``/``Downstre_1`` per-end inverts (79% on storm rows),
``Upstream_M``/``Downstream`` node-id labels (``8R1485``), ``Pipe_Size`` mm, ``Pipe_Type``
(RCP/...), and ``Sewer_Type`` splitting the systems — STORM + COMBINED join the storm graph
(ADR 0021; Windsor's core is combined) and SANITARY is the ADR 0011 tracer;
ABANDONED/PRIVATE rows stay out. The zip is fetched once per process via the shared
``sources._download`` cache and clipped to the AOI in-process. No point layers are
consumed in this first cut (land-cover imperviousness + junction delineation).

Elevation semantics (per the #157 convention — verified 2026-07-28 on the live dump):
  * ``Upstream_E``/``Downstre_1`` = pipe-end INVERTS, m AMSL inside a (150, 220) band
    (Detroit River ~174 m; ~14% of storm rows carry the 0 sentinel).
"""
import json

from swmmcanada.sources import _download
from swmmcanada.sources.cities import base

PIPELINE_ZIP = "https://opendata.citywindsor.ca/Uploads/Sewer%20Pipeline.zip"
_ZIP_MEMBER = "Sewer Pipeline/Sewer Pipeline.shp"

WINDSOR_CRS = "EPSG:32617"  # UTM 17N (metric ops; the dump itself ships EPSG:26917)

_STORM_TYPES = ("STORM", "COMBINED")   # combined joins storm (ADR 0021)
_SAN_TYPES = ("SANITARY",)


def _load_pipes(bbox, sewer_types) -> list:
    """Download-once, read the shapefile, clip to bbox, filter Sewer_Type -> GeoJSON
    features (the geometry work stays in geopandas; the builder sees plain features)."""
    import geopandas as gpd

    path = _download.fetch_file(PIPELINE_ZIP, cache_name="windsor_sewer_pipeline.zip")
    gdf = gpd.read_file(f"zip://{path}!{_ZIP_MEMBER}").to_crs(4326)
    min_lon, min_lat, max_lon, max_lat = bbox
    clip = gdf.cx[min_lon:max_lon, min_lat:max_lat]
    clip = clip[clip["Sewer_Type"].isin(sewer_types)]
    # keep only the consumed columns — the dump also carries Timestamp columns
    # (Installati...) that json.dumps cannot serialise
    keep = ["Compkey", "Pipe_Numbe", "Sewer_Type", "Pipe_Type", "Pipe_Shape",
            "Pipe_Size", "Pipe_Lengt", "Upstream_E", "Downstre_1",
            "Upstream_M", "Downstream", "geometry"]
    clip = clip[[c for c in keep if c in clip.columns]]
    return json.loads(clip.to_json())["features"]


def fetch_windsor_storm(bbox, *, client=None) -> dict:
    """``client`` is accepted for registry symmetry; the source is a file download."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    return {"mains": _load_pipes(bbox, _STORM_TYPES)}


def fetch_windsor_sanitary(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    return {"mains": _load_pipes(bbox, _SAN_TYPES)}


def fetch_windsor_land(bbox, *, client=None) -> dict:
    """First cut consumes no land dumps (parcel/building zips exist on the portal —
    future enrichment); subcatchments fall back to junction delineation."""
    return {"catchbasins": [], "parcels": [], "buildings": []}


# Plausible invert band (m AMSL): the Detroit River sits ~174 m; Windsor tops ~200 m.
_INVERT_MIN, _INVERT_MAX = 150.0, 220.0


def _elev(v):
    f = base.num(v, zero_missing=True)
    return f if (f is not None and _INVERT_MIN <= f <= _INVERT_MAX) else None


_line_ends = base.line_ends

_WINDSOR_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_windsor_network(data, *, config: base.AssembleConfig = _WINDSOR_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes; Upstream_M/Downstream ids label the snapped nodes; Sewer_Type
    is counted in the histogram."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)

    pipes, label_points = [], []
    n_no_geom = 0
    type_hist: dict = {}
    seen_names: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        st = str(p.get("Sewer_Type") or "?")
        type_hist[st] = type_hist.get(st, 0) + 1
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        name = str(p.get("Compkey") or p.get("Pipe_Numbe") or f.get("id"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{seen_names[name]}"
        dia_mm = base.num(p.get("Pipe_Size"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("Upstream_E")), inv_b=_elev(p.get("Downstre_1")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(
                {"RCP": "CONC"}.get(str(p.get("Pipe_Type") or "").upper(), p.get("Pipe_Type")),
                config.default_roughness),
            length_m=base.num(p.get("Pipe_Lengt"), zero_missing=True),
            shape=p.get("Pipe_Shape"),
        ))
        for xy, tid in ((a, p.get("Upstream_M")), (b, p.get("Downstream"))):
            tid = str(tid or "").strip()
            if tid:
                label_points.append((xy, tid))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(pipes, label_points=label_points, config=config)
    diag = {**result.diagnostics, "city": "windsor", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "sewer_type_histogram": type_hist,
            "n_combined_included": type_hist.get("COMBINED", 0),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
