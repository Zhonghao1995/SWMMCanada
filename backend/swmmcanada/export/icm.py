"""InfoWorks ICM exporter — an ODIC import package for an InfoWorks network (ADR 0012).

Reads the model-ready datastore (never the ``.inp``) and writes what ICM's **Open Data
Import Centre** consumes:

* ``nodes.csv`` / ``conduits.csv`` — columns named EXACTLY as the InfoWorks database fields
  (``node_id``, ``us_node_id``, ``conduit_width``, …) so ODIC's **Auto-Map Fields** button
  fills the assignment grid by itself. CSV, not shapefile: ODIC builds node geometry from
  ``x,y`` and draws links between nodes, and CSV headers dodge the DBF 10-char truncation
  that would break Auto-Map.
* ``subcatchments.shp`` — polygons are load-bearing, so shapefile with DBF-safe short names
  mapped via the sheet (``cn`` → ``curve_number``, …).
* ``rain_infoworks.csv`` — the "InfoWorks format CSV" rainfall event (one-click import);
  ``rain.csv`` — the plain fallback.
* ``field_mapping.md`` + ``README.md`` — mapping receipt, lossy report, import steps.

The headline difference from MIKE+ (ADR 0008): InfoWorks subcatchments natively carry an SCS
``curve_number``, so the SWMM CN transfers **losslessly** — there is no Horton approximation
here. Units are the ADR 0012 traps: conduit width/height in **mm**, areas in **ha**, slope in
m/m, Manning's n as-is with ``roughness_type = N``.

Note for ICM users who only want the model *inside* ICM: dual-engine ICM imports the shipped
``model.inp`` directly as a SWMM network, zero loss — this package is for InfoWorks-native
networks (ICM engine / 2D / the classic InfoWorks toolchain). The README says so too.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Optional

import geopandas as gpd
from shapely.geometry import Polygon

from swmmcanada.build.models import filter_system
from swmmcanada.export._shared import node_lookup, placeholder_square, to_crs, write_rain_csv
from swmmcanada.export.base import ExportResult, LossyMapping

_PI = 3.141592653589793


class IcmExporter:
    """Write an InfoWorks ICM ODIC import package from the datastore (ADR 0012)."""

    target = "icm"

    def export(self, ds, out_dir) -> ExportResult:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        crs = ds.config.get("coordinate_crs")  # e.g. "EPSG:32610"; None → keep EPSG:4326
        network = filter_system(ds.network)    # v1 exports the storm system only (ADR 0011)
        node_xy = node_lookup(network)

        lossy: List[LossyMapping] = _hydrology_lossy(ds)
        warnings: List[str] = []
        files: List[Path] = []

        files.append(_write_nodes(out / "nodes.csv", network, crs))
        files.append(_write_conduits(out / "conduits.csv", network, warnings))
        files.append(_write_subcatchments(out / "subcatchments.shp", ds, network, node_xy,
                                          crs, lossy, warnings))
        files.append(_write_rain_event(out / "rain_infoworks.csv", ds.rain, warnings))
        files.append(write_rain_csv(out / "rain.csv", ds.rain))
        files.append(_write_field_mapping(out / "field_mapping.md", lossy))
        files.append(_write_readme(out / "README.md"))

        return ExportResult(
            target=self.target, out_dir=out, files=files, lossy=lossy, warnings=warnings
        )


def export_icm(datastore_dir, out_dir) -> ExportResult:
    """Read a datastore directory and write its ICM ODIC import package into ``out_dir``."""
    from swmmcanada.datastore import read_datastore

    return IcmExporter().export(read_datastore(datastore_dir), out_dir)


# --------------------------------------------------------------------------- #
# nodes.csv / conduits.csv  (column names == InfoWorks DB fields → ODIC Auto-Map)
# --------------------------------------------------------------------------- #
def _write_nodes(path: Path, network, crs: Optional[str]) -> Path:
    """``x,y`` in the datastore's projected display CRS (the ICM network's coordinate
    system); levels in m AD. ``ground_level = invert + max_depth`` and ``chamber_floor =
    invert`` — the same construction the MIKE+ package uses for ``GroundLev``."""
    tf = _xy_transform(crs)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["node_id", "x", "y", "node_type", "system_type",
                    "ground_level", "chamber_floor"])
        for j in network.junctions:
            x, y = tf(float(j.x), float(j.y))
            w.writerow([j.name, x, y, "Manhole", "storm",
                        float(j.invert_m) + float(j.max_depth_m), float(j.invert_m)])
        for o in network.outfalls:
            x, y = tf(float(o.x), float(o.y))
            w.writerow([o.name, x, y, "Outfall", "storm",
                        float(o.invert_m), float(o.invert_m)])
    return path


def _write_conduits(path: Path, network, warnings: List[str]) -> Path:
    """No geometry column — ICM draws each link between its imported end nodes. Width and
    height are **mm** (the ADR 0012 ×1000 trap); inverts are the end-node inverts (SWMM
    conduits carry zero offsets in this datastore). ``link_suffix`` distinguishes parallel
    pipes between the same node pair (single character, per the InfoWorks field spec)."""
    inverts = {n.name: float(n.invert_m)
               for n in list(network.junctions) + list(network.outfalls)}
    suffix_pool = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    seen_pairs: dict = {}
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["us_node_id", "ds_node_id", "link_suffix", "conduit_length", "shape",
                    "conduit_width", "conduit_height", "us_invert", "ds_invert",
                    "roughness_type", "bottom_roughness_N", "top_roughness_N", "system_type"])
        for c in network.conduits:
            pair = (c.from_node, c.to_node)
            idx = seen_pairs.get(pair, 0)
            seen_pairs[pair] = idx + 1
            if idx >= len(suffix_pool):  # >35 parallel pipes between one node pair
                warnings.append(f"conduit {c.name}: link_suffix pool exhausted for {pair}")
                idx = len(suffix_pool) - 1
            width_mm = float(c.diameter_m) * 1000.0  # metres → millimetres (ADR 0012)
            w.writerow([c.from_node, c.to_node, suffix_pool[idx], float(c.length_m), "CIRC",
                        width_mm, width_mm,
                        inverts.get(c.from_node, ""), inverts.get(c.to_node, ""),
                        "N", float(c.roughness_n), float(c.roughness_n), "storm"])
    return path


def _xy_transform(crs: Optional[str]):
    """(lon, lat) → the display CRS the rest of the package (and the ICM network) uses."""
    if not crs:
        return lambda x, y: (x, y)
    from pyproj import Transformer

    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return lambda x, y: tr.transform(x, y)


# --------------------------------------------------------------------------- #
# subcatchments.shp  (DBF-safe short names; the sheet maps them to InfoWorks fields)
# --------------------------------------------------------------------------- #
def _write_subcatchments(path: Path, ds, network, node_xy, crs: Optional[str],
                         lossy: List[LossyMapping], warnings: List[str]) -> Path:
    keep = {n.name for n in list(network.junctions) + list(network.outfalls)}
    cols: dict = {"sub_id": [], "node_id": [], "total_area": [], "slope": [],
                  "dimension": [], "cn": [], "imp_pct": [], "prv_pct": [], "system_typ": [],
                  # ADR 0013 superset: Horton + Green-Ampt parameter sets ride along so any
                  # ICM runoff-volume choice (SCS / Horton / GreenAmpt) has its numbers.
                  "hort_f0": [], "hort_fc": [], "hort_decay": [],
                  "ga_psi_mm": [], "ga_ksat": [], "ga_imd": []}
    geom = []
    placeholders = 0
    for s in ds.subcatchments:
        if s.outlet_node not in keep:  # drains to a non-storm node (ADR 0011 filter)
            warnings.append(f"subcatchment {s.name}: outlet {s.outlet_node} not in the "
                            f"storm system; omitted")
            continue
        area_m2 = float(s.area_ha) * 10000.0
        cols["sub_id"].append(s.name)
        cols["node_id"].append(s.outlet_node)
        cols["total_area"].append(float(s.area_ha))          # InfoWorks CA units = ha
        cols["slope"].append(float(s.pct_slope) / 100.0)     # % → m/m
        cols["dimension"].append((area_m2 / _PI) ** 0.5)     # ICM's equal-area-circle radius
        cols["cn"].append(float(s.cn))                       # → curve_number, lossless
        cols["imp_pct"].append(float(s.pct_imperv))
        cols["prv_pct"].append(100.0 - float(s.pct_imperv))
        cols["system_typ"].append("storm")
        cols["hort_f0"].append(float(s.horton_f0_mm_h))      # mm/h
        cols["hort_fc"].append(float(s.horton_fc_mm_h))      # mm/h
        cols["hort_decay"].append(float(s.horton_decay_1_h)) # 1/h
        cols["ga_psi_mm"].append(float(s.ga_psi_mm))         # mm
        cols["ga_ksat"].append(float(s.ga_ksat_mm_h))        # mm/h
        cols["ga_imd"].append(float(s.ga_imd))               # fraction
        if s.polygon:
            geom.append(Polygon([(float(x), float(y)) for x, y in s.polygon]))
        else:
            geom.append(placeholder_square(area_m2, node_xy.get(s.outlet_node)))
            placeholders += 1

    if placeholders:
        lossy.append(LossyMapping(
            source="polygon", target="subcatchment boundary", kind="approximated",
            detail="no delineated polygon; placeholder square from area",
        ))

    gdf = gpd.GeoDataFrame(cols, geometry=geom, crs="EPSG:4326")
    to_crs(gdf, crs).to_file(path)
    return path


# --------------------------------------------------------------------------- #
# rainfall — the InfoWorks event CSV + the plain fallback
# --------------------------------------------------------------------------- #
def _write_rain_event(path: Path, rain, warnings: List[str]) -> Path:
    """The "InfoWorks format CSV" rainfall event: ``Import InfoWorks | Rainfall event |
    from InfoWorks format CSV file``. ``U_VALUES = mm`` — depth per timestep, which is what
    the datastore series carries. Grammar follows Innovyze's Events-CSV documentation; it
    cannot be validated without a licensed ICM, so the plain ``rain.csv`` ships alongside
    and the first import is the HITL verification step (ADR 0012 §5)."""
    fmt = "%d-%m-%Y %H:%M"
    step_s = 3600
    if len(rain.timestamps) > 1:
        step_s = int((rain.timestamps[1] - rain.timestamps[0]).total_seconds()) or 3600
        deltas = {int((b - a).total_seconds())
                  for a, b in zip(rain.timestamps, rain.timestamps[1:])}
        if len(deltas) > 1:
            warnings.append(f"rain_infoworks.csv: non-uniform timestep {sorted(deltas)}; "
                            f"G_TS uses the first interval, P_DATETIME rows carry the truth")
    lines = [
        "!Version=2,type=RED,charset=UTF8",
        "FILECONT,TITLE",
        "0,SWMMCanada rainfall",
        "UserSettings,U_VALUES,U_DATETIME",
        "UserSettingsValues,mm,dd-mm-yyyy hh:mm",
        "G_START,G_TS,G_NPROFILES,G_ARD,G_EVAP",
        f"{rain.timestamps[0].strftime(fmt)},{max(1, step_s // 60)}m,1,0,0",
        "L_PTITLE",
        rain.ts_name or "rain",
        "P_DATETIME,1",
    ]
    lines += [f"{ts.strftime(fmt)},{float(mm)}"
              for ts, mm in zip(rain.timestamps, rain.precip_mm)]
    path.write_text("\n".join(lines) + "\n")
    return path


# --------------------------------------------------------------------------- #
# lossy report + docs
# --------------------------------------------------------------------------- #
def _hydrology_lossy(ds) -> List[LossyMapping]:
    """What an InfoWorks network cannot take from a data import. NOTE the absence of the
    MIKE+ headline: ``cn`` → ``curve_number`` is native and lossless in ICM."""
    lossy = [
        LossyMapping(
            source="n_imperv/n_perv", target="runoff surface (dialog)", kind="restructured",
            detail="per-surface Manning n lives on ICM runoff-surface definitions, not on the "
                   "subcatchment import; set when creating the two runoff surfaces (README "
                   "recipe).",
        ),
        LossyMapping(
            source="s_imperv_mm/s_perv_mm", target="runoff surface initial loss",
            kind="restructured",
            detail="depression storage becomes the runoff surfaces' initial loss (mm), set in "
                   "the same dialog.",
        ),
        LossyMapping(
            source="pct_imperv", target="area_percent_1/2", kind="restructured",
            detail="SWMM impervious/pervious split carried as two runoff-area percentages "
                   "(ICM supports up to 12; 2 used).",
        ),
        LossyMapping(
            source="width_m", target="catchment_dimension", kind="restructured",
            detail="SWMM overland-flow width has no InfoWorks equivalent; catchment_dimension "
                   "is written using ICM's own convention (radius of the equal-area circle).",
        ),
        LossyMapping(
            source="pct_zero", target="—", kind="dropped",
            detail="no InfoWorks 'percent of impervious area with zero depression storage' "
                   "concept; dropped.",
        ),
    ]
    if ds.evaporation is not None:
        lossy.append(LossyMapping(
            source="evaporation series", target="—", kind="dropped",
            detail="an InfoWorks rainfall event carries only a scalar G_EVAP; the evaporation "
                   "time series is not imported — set ICM evaporation manually if needed.",
        ))
    if ds.temperature is not None:
        lossy.append(LossyMapping(
            source="temperature series", target="—", kind="dropped",
            detail="temperature/snowmelt forcing is not part of this ODIC package.",
        ))
    return lossy


def _write_field_mapping(path: Path, lossy: List[LossyMapping]) -> Path:
    rows = [
        ("`nodes.csv` (all columns)", "node fields",
         "**Auto-Map** — names equal the InfoWorks DB fields"),
        ("`invert_m` + `max_depth_m`", "`ground_level`", "sum (m AD)"),
        ("`invert_m`", "`chamber_floor` / outfall levels", "as-is (m AD)"),
        ("`conduits.csv` (all columns)", "conduit fields", "**Auto-Map**"),
        ("`diameter_m`", "`conduit_width` + `conduit_height`", "**×1000 (m → mm)**"),
        ("`roughness_n`", "`bottom/top_roughness_N` (`roughness_type=N`)", "as-is (Manning n)"),
        ("`length_m`", "`conduit_length`", "authoritative, not geometry-derived (m)"),
        ("`sub_id`", "`subcatchment_id`", "as-is"),
        ("`node_id` (shp)", "`node_id` (drains to)", "as-is"),
        ("`total_area`", "`total_area` + `contributing_area`", "as-is (ha)"),
        ("`slope`", "`catchment_slope`", "already ÷100 (% → m/m)"),
        ("`dimension`", "`catchment_dimension`", "√(area/π), ICM's own convention (m)"),
        ("`cn`", "`curve_number`", "**as-is — lossless** (SCS runoff volume model)"),
        ("`hort_f0` / `hort_fc` / `hort_decay`", "Horton runoff-volume parameters",
         "as-is (mm/h, mm/h, 1/h) — assign when choosing ICM's Horton model"),
        ("`ga_psi_mm` / `ga_ksat` / `ga_imd`", "Green-Ampt runoff-volume parameters",
         "as-is (mm, mm/h, fraction) — assign when choosing ICM's Green-Ampt model"),
        ("`imp_pct` / `prv_pct`", "`area_percent_1` / `area_percent_2`", "as-is (%)"),
        ("`system_typ`", "`system_type`", "as-is (`storm`)"),
    ]
    lines: List[str] = []
    lines.append("# InfoWorks ICM field mapping (ODIC import package) — ADR 0012\n")
    lines.append("> **Storm system only:** models carrying additional tagged systems "
                 "(e.g. a separated sanitary subgraph) export their storm_minor elements here.\n")
    lines.append("> `nodes.csv` and `conduits.csv` column names equal the InfoWorks database "
                 "fields — use ODIC's **Auto-Map Fields** button. The subcatchment shapefile "
                 "needs the manual assignments below (DBF names are capped at 10 chars).\n")
    lines.append("## Source column → InfoWorks field\n")
    lines.append("| source | InfoWorks field | conversion |")
    lines.append("|---|---|---|")
    for src, tgt, conv in rows:
        lines.append(f"| {src} | {tgt} | {conv} |")
    lines.append("")
    lines.append("## Lossy / approximated / ICM-side setup\n")
    lines.append("| source | target | kind | detail |")
    lines.append("|---|---|---|---|")
    for m in lossy:
        detail = m.detail.replace("|", "\\|")
        lines.append(f"| `{m.source}` | {m.target} | {m.kind} | {detail} |")
    lines.append("")
    path.write_text("\n".join(lines))
    return path


def _write_readme(path: Path) -> Path:
    text = """# InfoWorks ICM import package (ADR 0012)

This folder is an **InfoWorks ICM Open Data Import Centre (ODIC) package** produced from the
SWMMCanada model-ready datastore, targeting an **InfoWorks network** (the ICM engine / 2D /
classic InfoWorks toolchain).

> **Just want the model inside ICM?** Dual-engine ICM imports the package root's `model.inp`
> directly (`Import > SWMM v5 network`) as a **SWMM network** — zero loss, nothing from this
> folder needed. Use this folder when you want an InfoWorks-*native* network.

## Contents

- `nodes.csv` — manholes + outfalls (`x,y` in the package CRS; levels m AD)
- `conduits.csv` — circular conduits (**width/height in mm**, Manning `roughness_type=N`)
- `subcatchments.shp` — subcatchment polygons + attributes (`cn` → `curve_number`, lossless; Horton + Green-Ampt parameter sets included for those runoff-volume models)
- `rain_infoworks.csv` — rainfall event in InfoWorks CSV format (one-click import)
- `rain.csv` — plain `datetime,rainfall_mm` fallback
- `field_mapping.md` — the mapping receipt **and** the lossy report

## How to import (InfoWorks network)

1. Create/open an InfoWorks network. Open the **Open Data Import Centre**
   (`Network > Import > Open Data Import Centre`).
2. Table **Node**, data source `nodes.csv` → **Auto-Map Fields** → Import.
3. Table **Conduit**, data source `conduits.csv` → **Auto-Map Fields** → Import
   (links draw themselves between the imported nodes).
4. Table **Subcatchment**, data source `subcatchments.shp` → assign fields per
   `field_mapping.md` (DBF truncation prevents auto-map here) → Import.
5. Rainfall: `Import > InfoWorks > Rainfall event > from InfoWorks format CSV file` →
   `rain_infoworks.csv`. If your ICM version rejects the file, paste `rain.csv` into a new
   rainfall event grid instead — and please report it so the writer gets fixed.
6. **Runoff setup (ICM-side, one dialog):** create a land use with two runoff surfaces —
   *impervious*: fixed runoff coefficient, initial loss = `s_imperv_mm`; *pervious*:
   **US SCS** runoff volume using the imported `curve_number`, initial loss = `s_perv_mm`;
   assign `area_percent_1/2` (already imported). Review before running — this is the one
   step the import cannot do for you.

## Verification status

CI validates this package's structure and unit conversions (mm widths, ha areas, CN
pass-through). The first import into a licensed ICM is the manual verification step —
see the repo's tracking issue.
"""
    path.write_text(text)
    return path
