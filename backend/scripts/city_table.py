"""Eight-city validation table — the paper's breadth evidence, reproducible in one command.

For each real-network city this builds a FIXED downtown AOI end-to-end (live open data),
runs the EPA SWMM 5.2 engine on the produced model.inp, and collects one table row:

  scale     AOI km2, storm junctions / conduits / outfalls, subcatchments
  structure topology source, % node inverts gap-filled (vs published)
  method    subcatchment method actually used (parcel / voronoi / DEM, per the honesty gate)
  engine    SWMM ERROR count, runoff + flow-routing continuity (%), precip -> runoff depth (mm)
  extras    sanitary tracer present (junctions/conduits), evaporation on

Victoria and Ottawa use the same AOIs as the paper's Table 2 (number continuity); the other
six use ~1 km2 boxes centred on the downtown points the integration tests already exercise.
A city whose upstream portal fails mid-run gets a FAILED row with the reason — partial
tables are honest tables.

Run (from the repo root; EPA SWMM engine `swmm5` must be on PATH):

  backend/.venv/bin/python backend/scripts/city_table.py --out docs/paper/city-table

Outputs into --out: city_table.csv, city_table.md, per-city build workspaces
(<city>/model.inp, model.rpt, datastore/, validation.json, preview/) and diag_<city>.json
with the full recorded diagnostics. Rendering the companion figure: city_figure.py.
"""
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import build_city

START, END = date(2022, 6, 1), date(2022, 6, 7)   # same week as the paper's builds


def _box(min_lon, min_lat, max_lon, max_lat):
    return (min_lon, min_lat, max_lon, max_lat)


def _centre(lon, lat, d=0.005):
    return (lon - d, lat - d, lon + d, lat + d)


# key, topology source (how the city publishes connectivity), fixed AOI bbox
CITIES = [
    ("victoria", "Explicit node IDs", _box(-123.375, 48.418, -123.360, 48.429)),   # paper Table 2 AOI
    ("ottawa", "Geometry-inferred", _box(-75.705, 45.410, -75.685, 45.425)),       # paper Table 2 AOI
    ("london", "Explicit node IDs", _centre(-81.25, 42.98)),
    ("kitchener", "Explicit manhole IDs", _centre(-80.49, 43.45)),
    ("calgary", "Geometry-inferred", _centre(-114.06, 51.05)),
    ("surrey", "Geometry-inferred", _centre(-122.82, 49.12)),
    ("kelowna", "Geometry-inferred", _centre(-119.47, 49.88)),
    ("regina", "Geometry-inferred", _centre(-104.61, 50.445)),
]

COLUMNS = [
    "city", "label", "topology", "area_km2",
    "junctions", "conduits", "outfalls", "subcatchments",
    "sub_method", "inverts_gapfilled_pct",
    "sanitary",
    "swmm_errors", "runoff_continuity_pct", "routing_continuity_pct",
    "precip_mm", "runoff_mm",
    "status",
]


def _aoi_for(bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
        [min_lon, max_lat], [min_lon, min_lat]]]})


def _run_swmm(inp: Path):
    """Run the EPA SWMM 5.2 engine; return (rpt_text, n_errors)."""
    rpt, out = inp.with_suffix(".rpt"), inp.with_suffix(".out")
    proc = subprocess.run(["swmm5", str(inp), str(rpt), str(out)],
                          capture_output=True, text=True, timeout=600)
    text = rpt.read_text() if rpt.exists() else proc.stdout + proc.stderr
    n_errors = len(re.findall(r"(?m)^\s*ERROR\b", text))
    if proc.returncode != 0 and n_errors == 0:
        n_errors = 1   # engine died without writing ERROR lines — still a failure
    return text, n_errors


def _continuity(rpt_text):
    """(runoff %, flow-routing %) from the rpt continuity blocks, in report order."""
    vals = [float(v) for v in
            re.findall(r"Continuity Error \(%\)\s*\.+\s*(-?\d+\.?\d*)", rpt_text)]
    return (vals[0] if vals else None), (vals[1] if len(vals) > 1 else None)


def _depth_mm(rpt_text, row_label):
    """Depth (mm) column of a Runoff Quantity Continuity row, e.g. 'Total Precipitation'."""
    m = re.search(rf"{row_label}\s*\.+\s*[-\d.]+\s+([-\d.]+)", rpt_text)
    return float(m.group(1)) if m else None


def _counts_from_preview(ws: Path):
    """Storm-system element counts from the build's preview GeoJSON (sanitary excluded
    by its `system` tag; outfalls carry no tag, so the SAN_ merge prefix filters them)."""
    gj = json.loads((ws / "preview" / "network.geojson").read_text())
    j = c = o = s = 0
    for f in gj.get("features", []):
        kind = f["properties"].get("kind")
        system = f["properties"].get("system", "storm_minor")
        name = str(f["properties"].get("id", ""))
        if kind == "junction" and system != "sanitary":
            j += 1
        elif kind == "conduit" and system != "sanitary":
            c += 1
        elif kind == "outfall" and not name.startswith("SAN_"):
            o += 1
        elif kind == "subcatchment":
            s += 1
    return j, c, o, s


def build_one(key, topology, bbox, out_root: Path) -> dict:
    from swmmcanada.sources.cities.registry import city_spec

    label = city_spec(key).label
    ws = out_root / key
    ws.mkdir(parents=True, exist_ok=True)
    aoi = _aoi_for(bbox)
    row = {k: "" for k in COLUMNS}
    row.update(city=key, label=label, topology=topology, area_km2=round(aoi.area_km2, 2))

    try:
        res = build_city(key, aoi, START, END, ws)
    except Exception as exc:  # noqa: BLE001 — upstream outage -> honest FAILED row
        row.update(status=f"FAILED: {type(exc).__name__}: {exc}")
        return row

    prov = json.loads((ws / "datastore" / "datastore.json").read_text()).get("provenance", {})
    net_diag = prov.get("network_diagnostics", {})
    sub_diag = prov.get("subcatchment_diagnostics", {})
    sanitary = prov.get("sanitary", {})
    (out_root / f"diag_{key}.json").write_text(json.dumps(
        {"provenance": prov, "aoi_bbox": list(bbox)}, indent=2))

    j, c, o, s = _counts_from_preview(ws)
    n_nodes = net_diag.get("n_nodes") or 0
    n_fill = net_diag.get("n_inverts_gapfilled") or 0
    rpt_text, n_errors = _run_swmm(res.inp_path)
    runoff_cont, routing_cont = _continuity(rpt_text)

    row.update(
        junctions=j, conduits=c, outfalls=o, subcatchments=s,
        sub_method=sub_diag.get("method", "?"),
        inverts_gapfilled_pct=round(100.0 * n_fill / n_nodes, 1) if n_nodes else "",
        sanitary=(f"yes ({sanitary.get('n_junctions', '?')} J / {sanitary.get('n_conduits', '?')} C)"
                  if sanitary.get("included") else "no"),
        swmm_errors=n_errors,
        runoff_continuity_pct=runoff_cont, routing_continuity_pct=routing_cont,
        precip_mm=_depth_mm(rpt_text, "Total Precipitation"),
        runoff_mm=_depth_mm(rpt_text, "Surface Runoff"),
        status="ok" if n_errors == 0 else "engine errors",
    )
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="docs/paper/city-table", help="output/archive directory")
    ap.add_argument("--only", nargs="*", help="subset of city keys (default: all 8)")
    args = ap.parse_args()

    if shutil.which("swmm5") is None:
        sys.exit("swmm5 (EPA SWMM engine) is not on PATH — install it first.")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    todo = [(k, t, b) for (k, t, b) in CITIES if not args.only or k in args.only]

    # A partial re-run (--only, e.g. retrying one city after an upstream outage) merges
    # into the existing table instead of clobbering the other cities' rows.
    existing: dict = {}
    csv_path = out_root / "city_table.csv"
    if args.only and csv_path.exists():
        with csv_path.open() as f:
            existing = {r["city"]: r for r in csv.DictReader(f)}

    for i, (key, topology, bbox) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] building {key} ...", flush=True)
        row = build_one(key, topology, bbox, out_root)
        print(f"    -> {row['status']} | J/C/O={row['junctions']}/{row['conduits']}/{row['outfalls']}"
              f" | cont={row['runoff_continuity_pct']}/{row['routing_continuity_pct']}", flush=True)
        existing[key] = row

    rows = [existing[k] for (k, _, _) in CITIES if k in existing]

    with (out_root / "city_table.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    md = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    md += ["| " + " | ".join(str(r[k]) for k in COLUMNS) + " |" for r in rows]
    (out_root / "city_table.md").write_text("\n".join(md) + "\n")
    print(f"\nWrote {out_root / 'city_table.csv'} and city_table.md")


if __name__ == "__main__":
    main()
