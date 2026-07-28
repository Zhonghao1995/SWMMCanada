"""City of Moncton sewer data -> SWMM ``NetworkIn`` — the first Atlantic-Canada city.

Moncton's network rides one public token-free ``Sewer_Agol3`` FeatureServer on the city
AGOL org (``E26PuSoie2Y7bbyI``): Sewer Main (4) splits by ``UNITTYPE`` — STM (storm),
COMB (combined; joins the storm graph per ADR 0021 — downtown Moncton's core is combined),
SANI (the ADR 0011 tracer) — with Manholes (3) and Storm Inlet (1). Provenance recorded
honestly: the service is public but NOT listed in the open.moncton.ca hub catalogue and
its licence field is empty (the Saskatoon precedent). Parcels + Buildings ride their own
org services.

The vertical is two-tier like New Westminster's: ``UPSELEV``/``DWNELEV`` on ~60-80% of
mains, and ``MAINKEY1``/``MAINKEY2`` join the Manholes' integer ``COMPKEY`` so missing
ends could lift the manhole (not needed yet — the manholes publish no chamber invert,
only ``ZTOPCOV``). Node labels come from MAINKEY ids (MH-prefixed).

Elevation semantics (per the #157 convention — verified live 2026-07-28):
  * ``UPSELEV``/``DWNELEV`` (mains) = pipe-end INVERTS, m AMSL (tidal Petitcodiac ~2 m up
    to ~70 m); 0 = missing.
  * ``ZTOPCOV`` (manholes) = top-of-cover/rim -> node max depths, plausibility-banded;
    ``MHDPTH`` (depth) deliberately not read.
"""
from swmmcanada.sources.cities import base

ORG = "https://services1.arcgis.com/E26PuSoie2Y7bbyI/arcgis/rest/services"
SEWER = "Sewer_Agol3"
MAINS, MANHOLES, INLETS = 4, 3, 1
PARCELS_SVC, BUILDINGS_SVC = "Parcels", "Buildings"

MONCTON_CRS = "EPSG:32620"  # UTM 20N (metric ops) — first city in this zone
_PAGE = 2000

_STORM_WHERE = "UNITTYPE IN ('STM','COMB')"   # combined joins storm (ADR 0021)
_SAN_WHERE = "UNITTYPE='SANI'"


MonctonClient = base.ArcGISClient


def _fetch(svc, layer, bbox, client, where="1=1") -> list:
    return base.fetch_paged(client, f"{ORG}/{svc}/FeatureServer/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_moncton_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or MonctonClient()
    return {
        "mains": _fetch(SEWER, MAINS, bbox, client, where=_STORM_WHERE),
        "manholes": _fetch(SEWER, MANHOLES, bbox, client),
    }


def fetch_moncton_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary mains — the second tagged system (ADR 0011)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or MonctonClient()
    return {
        "mains": _fetch(SEWER, MAINS, bbox, client, where=_SAN_WHERE),
        "manholes": _fetch(SEWER, MANHOLES, bbox, client),
    }


def fetch_moncton_land(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or MonctonClient()
    return {
        "catchbasins": _fetch(SEWER, INLETS, bbox, client),
        "parcels": _fetch(PARCELS_SVC, 0, bbox, client),
        "buildings": _fetch(BUILDINGS_SVC, 0, bbox, client),
    }


def _elev(v):
    """Invert/rim -> float m AMSL, or None (0 = missing; the tidal bank sits >= ~2 m)."""
    return base.num(v, zero_missing=True)


# Plausible rim band (m AMSL): Petitcodiac bank ~2 m to the ridge ~90 m.
_RIM_MIN, _RIM_MAX = 0.5, 150.0


def _rim(v):
    f = _elev(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_line_ends = base.line_ends

_MONCTON_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_moncton_network(data, *, config: base.AssembleConfig = _MONCTON_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes; MAINKEY1/MAINKEY2 (MH-prefixed) label the snapped nodes; manhole
    ZTOPCOV rims drive max depths; UNITTYPE is counted in the histogram."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)
    manholes = _features((data or {}).get("manholes", []) if isinstance(data, dict) else [])

    ground_points = []
    for f in manholes:
        c = (f.get("geometry") or {}).get("coordinates") or []
        rim = _rim((f.get("properties") or {}).get("ZTOPCOV"))
        if len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    pipes, label_points = [], []
    n_no_geom = 0
    unittype_hist: dict = {}
    seen_names: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        ut = str(p.get("UNITTYPE") or "?")
        unittype_hist[ut] = unittype_hist.get(ut, 0) + 1
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        name = str(p.get("UNITID") or p.get("OBJECTID"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{p.get('OBJECTID')}"
        dia_mm = base.num(p.get("DIAMETER"), zero_missing=True)
        h_mm = base.num(p.get("HEIGHT"), zero_missing=True)
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("UPSELEV")), inv_b=_elev(p.get("DWNELEV")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(p.get("PIPETYPE"), config.default_roughness),
            shape=p.get("PIPESHP"),
            height_m=(h_mm / 1000.0) if h_mm else None,
            width_m=(dia_mm / 1000.0) if dia_mm else None,
        ))
        for xy, tid in ((a, p.get("MAINKEY1")), (b, p.get("MAINKEY2"))):
            tid = str(tid or "").strip()
            if tid and tid != "0":
                label_points.append((xy, f"MH{tid}"))

    label_points, n_lab_dup, n_lab_reserved = base.safe_labels(label_points, config.snap_decimals)

    result = base.assemble_network(
        pipes, ground_points=ground_points, label_points=label_points, config=config,
    )
    diag = {**result.diagnostics, "city": "moncton", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom, "n_rims_in": len(ground_points),
            "unittype_histogram": unittype_hist,
            "n_combined_included": unittype_hist.get("COMB", 0),
            "n_labels_dropped_nonunique": n_lab_dup, "n_labels_dropped_reserved": n_lab_reserved}
    return base.NetworkResult(network=result.network, diagnostics=diag)
