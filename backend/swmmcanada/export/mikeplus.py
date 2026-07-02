"""MIKE+ (Collection System) exporter — the first non-SWMM target (ADR 0008).

Reads the model-ready datastore (never the ``.inp``) and writes a MIKE+ CS **import
package** (Option B: importable files, not a native ``.sqlite``): ``nodes``/``links``/
``catchments`` shapefiles in the datastore's projected display CRS, a ``rain.csv`` time
series, and two markdown sheets (field mapping + how-to-import). Runoff maps onto MIKE+
**Model B** (non-linear reservoir / Kinematic Wave); the mapping is genuinely lossy —
SWMM's hydrology is CN/Horton-flavoured — so every approximation is surfaced as a
``LossyMapping`` rather than silently dropped (issue #5).

``rain.dfs0`` (the native MIKE time series) is deferred: ``mikecore``/``mikeio`` ship no
macOS wheel, so rainfall is emitted as CSV here and the field-mapping sheet says so.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

from swmmcanada.export.base import ExportResult, LossyMapping

# Horton infiltration constants (m/s) for the CN→Horton approximation (ADR 0008 §4). f0/wet/
# dry are fixed heuristics; only f∞ (HortMin) is steered by CN — a drier, lower-CN surface
# keeps a higher final infiltration capacity. These are a headline approximation, not a fit.
_HORTON_F0 = 2.1e-5
_HORTON_WET = 1.1e-3
_HORTON_DRY = 1.6e-5


def _horton_fmin(cn: float) -> float:
    """Final infiltration capacity f∞ (m/s), heuristically steered by curve number.

    ``(100-CN)`` (mm/h, clamped to [0.5, 13.0]) → m/s. Lower CN ⇒ more permeable ⇒ higher
    f∞. This is the load-bearing approximation MIKE+ users must review before running.
    """
    mm_per_h = max(0.5, min(13.0, (100.0 - cn) * 0.25))
    return mm_per_h / 3.6e6  # mm/h → m/s


def _recip(n, fallback: float, what: str, warnings: List[str]) -> float:
    """Manning's M = 1/n, defended: non-positive n (dirty upstream data) falls back to a
    stated default M with a warning, instead of a ZeroDivisionError killing the export."""
    if n and float(n) > 0:
        return 1.0 / float(n)
    warnings.append(f"{what}: non-positive Manning n ({n!r}); ManningM defaulted to {fallback}")
    return fallback


class MikePlusExporter:
    """Write a MIKE+ CS import package from the datastore (ADR 0008 Option B)."""

    target = "mikeplus"

    def export(self, ds, out_dir) -> ExportResult:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        crs = ds.config.get("coordinate_crs")  # e.g. "EPSG:32610"; None → keep EPSG:4326
        node_xy = _node_lookup(ds.network)  # name → (lon, lat) over junctions + outfalls

        lossy: List[LossyMapping] = list(_hydrology_lossy())
        warnings: List[str] = []
        files: List[Path] = []

        files.append(_write_nodes(out / "nodes.shp", ds.network, crs))
        files.append(_write_links(out / "links.shp", ds.network, node_xy, crs, warnings))
        files.append(_write_catchments(out / "catchments.shp", ds, node_xy, crs, lossy, warnings))
        files.append(_write_rain(out / "rain.csv", ds.rain))
        files.append(_write_field_mapping(out / "field_mapping.md", lossy))
        files.append(_write_readme(out / "README.md"))

        return ExportResult(
            target=self.target, out_dir=out, files=files, lossy=lossy, warnings=warnings
        )


def export_mikeplus(datastore_dir, out_dir) -> ExportResult:
    """Read a datastore directory and write its MIKE+ CS import package into ``out_dir``."""
    from swmmcanada.datastore import read_datastore

    return MikePlusExporter().export(read_datastore(datastore_dir), out_dir)


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _node_lookup(network) -> Dict[str, Tuple[float, float]]:
    """name → (lon, lat) over junctions + outfalls (link endpoints + catchment placeholders)."""
    xy: Dict[str, Tuple[float, float]] = {}
    for n in list(network.junctions) + list(network.outfalls):
        xy[n.name] = (float(n.x), float(n.y))
    return xy


def _to_crs(gdf: gpd.GeoDataFrame, crs: Optional[str]) -> gpd.GeoDataFrame:
    """Reproject 4326→``crs`` when set so ``.to_file`` writes a matching ``.prj``."""
    return gdf.to_crs(crs) if crs else gdf


# --------------------------------------------------------------------------- #
# shapefile writers  (DBF column names ≤ 10 chars)
# --------------------------------------------------------------------------- #
def _write_nodes(path: Path, network, crs: Optional[str]) -> Path:
    muid, ntype, invert, ground, diam, geom = [], [], [], [], [], []
    for j in network.junctions:
        muid.append(j.name)
        ntype.append("Manhole")
        invert.append(float(j.invert_m))
        ground.append(float(j.invert_m) + float(j.max_depth_m))
        diam.append(1.0)
        geom.append(Point(float(j.x), float(j.y)))
    for o in network.outfalls:
        muid.append(o.name)
        ntype.append("Outlet")
        invert.append(float(o.invert_m))
        ground.append(float(o.invert_m))
        diam.append(1.0)
        geom.append(Point(float(o.x), float(o.y)))

    gdf = gpd.GeoDataFrame(
        {"MUID": muid, "NodeType": ntype, "InvertLev": invert, "GroundLev": ground,
         "Diameter": diam},
        geometry=geom, crs="EPSG:4326",
    )
    _to_crs(gdf, crs).to_file(path)
    return path


def _write_links(path: Path, network, node_xy, crs: Optional[str], warnings: List[str]) -> Path:
    muid, frm, to, length, diam, mann, geom = [], [], [], [], [], [], []
    for c in network.conduits:
        muid.append(c.name)
        frm.append(c.from_node)
        to.append(c.to_node)
        length.append(float(c.length_m))  # authoritative length, not geometry-derived
        diam.append(float(c.diameter_m))
        mann.append(_recip(c.roughness_n, 75.0, f"link {c.name}", warnings))  # M = 1/n
        a = node_xy.get(c.from_node)
        b = node_xy.get(c.to_node)
        geom.append(LineString([a, b]) if a and b else None)

    gdf = gpd.GeoDataFrame(
        {"MUID": muid, "FromNode": frm, "ToNode": to, "Length": length,
         "Diameter": diam, "ManningM": mann},
        geometry=geom, crs="EPSG:4326",
    )
    _to_crs(gdf, crs).to_file(path)
    return path


def _write_catchments(path: Path, ds, node_xy, crs: Optional[str],
                      lossy: List[LossyMapping], warnings: List[str]) -> Path:
    cols: Dict[str, list] = {
        "MUID": [], "NodeID": [], "Area": [], "ImpervPct": [], "Length": [],
        "Slope": [], "ManMImp": [], "ManMPrv": [], "StorImp": [], "StorPrv": [],
        "HortMax": [], "HortMin": [], "HortWet": [], "HortDry": [], "Model": [],
    }
    geom = []
    placeholders = 0
    for s in ds.subcatchments:
        area_m2 = float(s.area_ha) * 10000.0
        cols["MUID"].append(s.name)
        cols["NodeID"].append(s.outlet_node)
        cols["Area"].append(area_m2)
        cols["ImpervPct"].append(float(s.pct_imperv))
        # Length = area/width; a non-positive width (dirty data) falls back to √area
        # (square catchment) with a warning rather than a ZeroDivisionError.
        if s.width_m and float(s.width_m) > 0:
            cols["Length"].append(area_m2 / float(s.width_m))
        else:
            warnings.append(
                f"catchment {s.name}: non-positive width_m ({s.width_m!r}); "
                f"Length defaulted to sqrt(area)")
            cols["Length"].append(area_m2 ** 0.5)
        cols["Slope"].append(float(s.pct_slope) / 100.0)
        cols["ManMImp"].append(_recip(s.n_imperv, 100.0, f"catchment {s.name} (imperv)", warnings))
        cols["ManMPrv"].append(_recip(s.n_perv, 10.0, f"catchment {s.name} (perv)", warnings))
        cols["StorImp"].append(float(s.s_imperv_mm) / 1000.0)
        cols["StorPrv"].append(float(s.s_perv_mm) / 1000.0)
        cols["HortMax"].append(_HORTON_F0)
        cols["HortMin"].append(_horton_fmin(float(s.cn)))
        cols["HortWet"].append(_HORTON_WET)
        cols["HortDry"].append(_HORTON_DRY)
        cols["Model"].append("B")

        if s.polygon:
            geom.append(Polygon([(float(x), float(y)) for x, y in s.polygon]))
        else:  # keep the layer all-Polygon: synthesise a square from area at the outlet
            geom.append(_placeholder_square(area_m2, node_xy.get(s.outlet_node)))
            placeholders += 1

    if placeholders:
        lossy.append(LossyMapping(
            source="polygon", target="catchment geometry", kind="approximated",
            detail="no delineated polygon; placeholder square from area",
        ))

    gdf = gpd.GeoDataFrame(cols, geometry=geom, crs="EPSG:4326")
    _to_crs(gdf, crs).to_file(path)
    return path


def _placeholder_square(area_m2: float, center_lonlat: Optional[Tuple[float, float]]) -> Polygon:
    """A square of side √area centred on the outlet node, built in 4326 (reprojected with
    the layer). Falls back to origin if the outlet node coordinate is unknown.

    The side is derived in metres but drawn as a degree offset — placeholder geometry only,
    never used for computation (``Area``/``Length`` come from the authoritative attributes).
    """
    lon, lat = center_lonlat if center_lonlat else (0.0, 0.0)
    half_deg = (area_m2 ** 0.5) / 111_320.0 / 2.0  # ~metres per degree at the equator
    return Polygon([
        (lon - half_deg, lat - half_deg),
        (lon + half_deg, lat - half_deg),
        (lon + half_deg, lat + half_deg),
        (lon - half_deg, lat + half_deg),
    ])


# --------------------------------------------------------------------------- #
# rainfall + docs
# --------------------------------------------------------------------------- #
def _write_rain(path: Path, rain) -> Path:
    """Two-column CSV (``datetime,rainfall_mm``, ISO datetimes). dfs0 deferred (ADR 0008)."""
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["datetime", "rainfall_mm"])
        for ts, mm in zip(rain.timestamps, rain.precip_mm):
            w.writerow([ts.isoformat(), float(mm)])
    return path


def _hydrology_lossy() -> List[LossyMapping]:
    """The datastore→MIKE+ Model B losses that always apply (ADR 0008 §4), independent of
    geometry. Placeholder-polygon entries are appended per-catchment separately."""
    return [
        LossyMapping(
            source="cn", target="Horton f0/f∞/wet/dry", kind="approximated",
            detail="SWMM Curve Number has no MIKE+ Model B equivalent; Horton infiltration "
                   "is heuristically derived from CN (f∞ from CN, f0/wet/dry fixed) — REVIEW "
                   "before running.",
        ),
        LossyMapping(
            source="pct_zero", target="—", kind="dropped",
            detail="MIKE+ Model B has no 'percent of impervious area with zero depression "
                   "storage' concept; dropped.",
        ),
        LossyMapping(
            source="s_imperv_mm/s_perv_mm", target="Storage Loss + Wetting Loss",
            kind="restructured",
            detail="SWMM's single depression-storage depth is carried as MIKE Storage Loss "
                   "(÷1000, mm→m); MIKE's separate Wetting Loss term is set to 0.",
        ),
        LossyMapping(
            source="pct_imperv", target="Contributing Area (2 surface types)",
            kind="restructured",
            detail="SWMM impervious/pervious split maps to MIKE+; MIKE's 5 native surface "
                   "types are collapsed to 2 (impervious + pervious).",
        ),
        LossyMapping(
            source="rainfall (dfs0)", target="rain.csv", kind="restructured",
            detail="Native MIKE .dfs0 time series is deferred (mikecore/mikeio have no macOS "
                   "wheel); rainfall is exported as CSV and imported as a time series instead.",
        ),
    ]


def _write_field_mapping(path: Path, lossy: List[LossyMapping]) -> Path:
    rows = [
        ("`invert_m` (junction/outfall)", "`InvertLev`", "as-is (m)"),
        ("`invert_m` + `max_depth_m`", "`GroundLev` (junction)", "sum (m)"),
        ("`length_m` (conduit)", "`Length`", "authoritative, not geometry-derived (m)"),
        ("`diameter_m` (conduit)", "`Diameter`", "as-is (m)"),
        ("`roughness_n` (conduit)", "`ManningM`", "**M = 1/n** (reciprocal)"),
        ("`area_ha`", "`Area`", "×10000 (ha→m²)"),
        ("`width_m` + `area_ha`", "`Length` (catchment)", "area_m² / width_m"),
        ("`pct_slope`", "`Slope`", "÷100 (%→m/m)"),
        ("`n_imperv` / `n_perv`", "`ManMImp` / `ManMPrv`", "**M = 1/n** (reciprocal)"),
        ("`s_imperv_mm` / `s_perv_mm`", "`StorImp` / `StorPrv`", "÷1000 (mm→m)"),
        ("`pct_imperv`", "`ImpervPct` (Contributing Area)", "as-is (%)"),
        ("`cn`", "`HortMin` (Horton f∞)", "**approximated** — heuristic from CN"),
    ]
    lines: List[str] = []
    lines.append("# MIKE+ CS field mapping (Model B / Kinematic Wave) — ADR 0008\n")
    lines.append("**Runoff = MIKE+ Model B (non-linear reservoir / Kinematic Wave).**\n")
    lines.append("> **Horton infiltration is APPROXIMATED from the SWMM Curve Number** and "
                 "should be reviewed before running the CS engine.\n")
    lines.append("> **Rainfall is exported as CSV** (`rain.csv`); the native MIKE `.dfs0` "
                 "carrier is deferred (`mikecore`/`mikeio` have no macOS wheel).\n")
    lines.append("## SWMM datastore field → MIKE+ Model B field\n")
    lines.append("| SWMM datastore field | MIKE+ Model B field | conversion |")
    lines.append("|---|---|---|")
    for src, tgt, conv in rows:
        lines.append(f"| {src} | {tgt} | {conv} |")
    lines.append("")
    lines.append("## Lossy / approximated\n")
    lines.append("| source | target | kind | detail |")
    lines.append("|---|---|---|---|")
    for m in lossy:
        detail = m.detail.replace("|", "\\|")
        lines.append(f"| `{m.source}` | {m.target} | {m.kind} | {detail} |")
    lines.append("")
    path.write_text("\n".join(lines))
    return path


def _write_readme(path: Path) -> Path:
    text = """# MIKE+ CS import package (ADR 0008)

This folder is a **DHI MIKE+ Collection System (CS) import package** produced from the
SWMMCanada model-ready datastore. Runoff is mapped onto **MIKE+ Model B (non-linear
reservoir / Kinematic Wave)** — the closest native-MIKE analogue to SWMM's subcatchment.

It is **Option B**: importable files, not a native `.sqlite`. You materialise the CS model
by running MIKE+'s own import.

## Contents

- `nodes.shp` — junctions (`NodeType=Manhole`) + outfalls (`NodeType=Outlet`)
- `links.shp` — conduits (`Length` authoritative, `ManningM = 1/n`)
- `catchments.shp` — subcatchments as Model B catchments (all-Polygon)
- `rain.csv` — rainfall time series (`datetime,rainfall_mm`)
- `field_mapping.md` — datastore→MIKE+ field map **and** the lossy/approximation report
- `README.md` — this file

All shapefiles carry a `.prj` in the datastore's projected display CRS.

## How to import into MIKE+ CS

1. Import `nodes.shp`, `links.shp`, and `catchments.shp` into MIKE+ CS, mapping the
   shapefile columns to the CS attributes using the table in `field_mapping.md`.
2. Import `rain.csv` as a rainfall time series and attach it as the catchment forcing.
3. **Review the Horton infiltration parameters** on the catchments — they are approximated
   from the SWMM Curve Number and must be checked before running the CS engine.

## Notes

- **Native `.dfs0` is deferred**: `mikecore`/`mikeio` ship no macOS wheel, so rainfall is a
  CSV here. Import it as a time series; a `.dfs0` writer can be added later with no rework.
- The datastore→MIKE mapping is genuinely lossy (SWMM hydrology is CN/Horton-flavoured);
  every approximation is listed in `field_mapping.md` rather than silently dropped.
"""
    path.write_text(text)
    return path
