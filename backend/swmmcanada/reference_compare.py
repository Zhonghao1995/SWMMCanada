"""Compare a municipally supplied reference SWMM model against our build of the same AOI.

Any city that reviews this platform may hand back a reference ``.inp``. This module turns
that gift into numbers, twice over:

* **comparison** — how far apart the two models are: node/conduit counts, invert profile
  difference, diameter distribution, partition statistics (unit count, median area,
  imperviousness composition);
* **reproduction** — whether our delineation reproduces theirs: per-unit outlet agreement
  (does the land they drain to node X drain to node X in our model too), per-node load bias
  (contributing area, impervious-weighted area, sanitary-side population), and per-node
  service-area IoU (geometric overlap of everything a node serves).

Both drainage sides are scored: the runoff partition against our surface catchments, and the
zero-area dry-weather-flow carriers against our sewer service areas (a zero-area
subcatchment produces no runoff — in a sewer model it exists to attach loading to a node,
which is exactly what our service areas are).

Nodes are joined by municipal asset ID, not geometry: reference models often name a manhole
``"MH" + <asset id>`` where the city's open data publishes the bare number, so ``MH123``
joins our ``123`` and otherwise names must match exactly.

The harness itself is public; its reference input is not. Reference models are confidential
local files — they are read here and never copied, and nothing derived from a real one may
enter the repository. The report is written for local analysis; only aggregate statistics
from it should ever be shared onward.

Missing pieces degrade, they do not crash: a reference without ``[POLYGONS]`` skips the
geometric metrics and says so in the report's ``degraded`` list; every number that can
still be computed is.

Usage (thin CLI wrapper in ``backend/scripts/reference_compare.py``)::

    reference_compare.py reference.inp path/to/result_package --inp-crs EPSG:26910 \
        [--json report.json]

The build side accepts either a result-package root (containing ``datastore/``) or a bare
datastore directory. ``--inp-crs`` names the projected CRS the ``.inp`` coordinates are in;
without it the geometric metrics are skipped (our datastore is EPSG:4326 and the two sides
must meet on one plane before areas can overlap).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.ops import transform as _shp_transform
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid
from swmm_api import read_inp_file

from swmmcanada import result_package
from swmmcanada.datastore import read_datastore
from swmmcanada.datastore.schema import DATASTORE_JSON

_MH = re.compile(r"(?i)^MH(\d+)$")


def canon_node(name) -> str:
    """The asset-ID join key: ``MH123`` -> ``123``; anything else joins by exact name."""
    s = str(name)
    m = _MH.match(s)
    return m.group(1) if m else s


@dataclass
class CompareUnit:
    """One land unit on either side — a reference subcatchment, our surface catchment, or
    a sewer service area. ``geometry`` is optional and must already be in the shared plane."""

    name: str
    outlet: str
    area_ha: float
    pct_imperv: float = 0.0
    geometry: Optional[object] = None


@dataclass
class ModelSide:
    """Everything one side contributes to the report, loader-agnostic. Node-keyed dicts
    are keyed by RAW node names; canonicalisation happens at comparison time."""

    label: str
    node_inverts: Dict[str, float] = field(default_factory=dict)
    n_junctions: int = 0
    n_outfalls: int = 0
    n_conduits: int = 0
    diameters_mm: List[int] = field(default_factory=list)   # circular conduits only
    n_noncircular: int = 0
    units_storm: List[CompareUnit] = field(default_factory=list)
    units_sanitary: List[CompareUnit] = field(default_factory=list)
    population_by_node: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    meta: Dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# small statistics helpers (None means "not computable", never a fake zero)
# --------------------------------------------------------------------------- #
def _dist(vals: List[float]) -> dict:
    if not vals:
        return {"n": 0, "mean": None, "median": None, "p10": None, "p90": None}
    if len(vals) == 1:
        v = float(vals[0])
        return {"n": 1, "mean": v, "median": v, "p10": v, "p90": v}
    q = statistics.quantiles(vals, n=10, method="inclusive")
    return {"n": len(vals), "mean": float(statistics.fmean(vals)),
            "median": float(statistics.median(vals)),
            "p10": float(q[0]), "p90": float(q[8])}


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    try:
        return float(statistics.correlation(xs, ys))
    except statistics.StatisticsError:        # constant input — correlation undefined
        return None


def _valid(geom):
    return geom if geom.is_valid else make_valid(geom)


def _f32grid(xs, ys):
    """Round coordinates through float32 — the precision swmm-api parses [POLYGONS] at."""
    return (np.asarray(xs, dtype=np.float32).astype(np.float64),
            np.asarray(ys, dtype=np.float32).astype(np.float64))


# --------------------------------------------------------------------------- #
# reproduction metrics (pure functions, one known answer each in the tests)
# --------------------------------------------------------------------------- #
def aggregate_by_node(units: List[CompareUnit],
                      value: Callable[[CompareUnit], float] = lambda u: u.area_ha,
                      ) -> Dict[str, float]:
    """Sum ``value(unit)`` per canonical outlet node — the per-node load a pipe answers to."""
    out: Dict[str, float] = {}
    for u in units:
        key = canon_node(u.outlet)
        out[key] = out.get(key, 0.0) + float(value(u))
    return out


def load_bias(ref_by_node: Dict[str, float], ours_by_node: Dict[str, float]) -> dict:
    """Per-node scalar comparison over the joined keys: correlation + bias distribution."""
    common = sorted(set(ref_by_node) & set(ours_by_node))
    xs = [float(ref_by_node[k]) for k in common]
    ys = [float(ours_by_node[k]) for k in common]
    ratios = [y / x for x, y in zip(xs, ys) if x > 0]
    return {
        "n_common": len(common),
        "n_ref_only": len(set(ref_by_node) - set(ours_by_node)),
        "n_ours_only": len(set(ours_by_node) - set(ref_by_node)),
        "pearson_r": _pearson(xs, ys),
        "diff": _dist([y - x for x, y in zip(xs, ys)]),
        "ratio_median": float(statistics.median(ratios)) if ratios else None,
        "per_node": {k: {"reference": x, "ours": y} for k, x, y in zip(common, xs, ys)},
    }


def outlet_agreement(ref_units: List[CompareUnit], our_units: List[CompareUnit]) -> dict:
    """For each reference land unit: does the land go to the same node in our model?

    "The same land" is decided by majority overlap — the one of our units that shares the
    most area with the reference unit speaks for it. A reference unit overlapping none of
    ours is *unmatched* (we did not delineate that ground), which is reported separately
    rather than counted as disagreement.
    """
    ref_geo = [u for u in ref_units if u.geometry is not None]
    our_geo = [u for u in our_units if u.geometry is not None]
    if not ref_geo or not our_geo:
        side = "reference" if not ref_geo else "ours"
        return {"skipped": f"no unit geometry on {side} side"}

    geoms = [_valid(u.geometry) for u in our_geo]
    tree = STRtree(geoms)
    n_agree = n_matched = 0
    mismatches = []
    for r in ref_geo:
        rg = _valid(r.geometry)
        best, best_area = None, 0.0
        for i in tree.query(rg):
            a = rg.intersection(geoms[int(i)]).area
            if a > best_area:
                best, best_area = our_geo[int(i)], a
        if best is None:
            continue
        n_matched += 1
        if canon_node(r.outlet) == canon_node(best.outlet):
            n_agree += 1
        else:
            mismatches.append({"unit": r.name, "ref_outlet": r.outlet,
                               "our_outlet": best.outlet, "our_unit": best.name})
    return {
        "n_ref_units": len(ref_units),
        "n_ref_with_geometry": len(ref_geo),
        "n_matched": n_matched,
        "n_unmatched": len(ref_geo) - n_matched,
        "n_agree": n_agree,
        "rate": (n_agree / n_matched) if n_matched else None,
        "mismatches": mismatches,
    }


def _union_by_node(units: List[CompareUnit]):
    grouped: Dict[str, list] = {}
    for u in units:
        if u.geometry is not None:
            grouped.setdefault(canon_node(u.outlet), []).append(_valid(u.geometry))
    return {node: unary_union(gs) for node, gs in grouped.items()}


def service_iou(ref_units: List[CompareUnit], our_units: List[CompareUnit]) -> dict:
    """Per common node: IoU of the union of everything the node serves, each side."""
    ref_by = _union_by_node(ref_units)
    our_by = _union_by_node(our_units)
    if not ref_by or not our_by:
        side = "reference" if not ref_by else "ours"
        return {"skipped": f"no unit geometry on {side} side"}
    common = sorted(set(ref_by) & set(our_by))
    per_node = {}
    for node in common:
        union = ref_by[node].union(our_by[node]).area
        if union > 0:
            per_node[node] = ref_by[node].intersection(our_by[node]).area / union
    return {
        "n_nodes_common": len(common),
        "n_ref_only": len(set(ref_by) - set(our_by)),
        "n_ours_only": len(set(our_by) - set(ref_by)),
        "per_node": per_node,
        "stats": _dist(list(per_node.values())),
    }


def partition_stats(units: List[CompareUnit]) -> dict:
    """Distribution statistics of one side's partition: how many units, how big, what mix."""
    areas = [u.area_ha for u in units]
    decades: Dict[str, float] = {}
    for u in units:
        lo = min(int(u.pct_imperv // 10) * 10, 90)
        key = f"{lo}-{lo + 10}"
        decades[key] = decades.get(key, 0.0) + 1.0
    return {
        "n": len(units),
        "total_area_ha": float(sum(areas)) if areas else 0.0,
        "median_area_ha": float(statistics.median(areas)) if areas else None,
        "mean_area_ha": float(statistics.fmean(areas)) if areas else None,
        "imperv_decade_share": {k: v / len(units) for k, v in sorted(decades.items())},
    }


# --------------------------------------------------------------------------- #
# loaders: the reference .inp (via swmm-api) and our build output
# --------------------------------------------------------------------------- #
def _section(inp, key):
    """A parsed section, or {} when absent. swmm-api converts sections lazily on
    ``__getitem__`` — ``.get()`` would hand back the raw unparsed text."""
    return (inp[key] or {}) if key in inp else {}


def read_reference_model(inp_path) -> ModelSide:
    """Parse the reference ``.inp`` into a :class:`ModelSide`. Missing sections become
    notes, not exceptions — a geometry-and-parameters reference is still comparable."""
    inp = read_inp_file(str(inp_path))
    side = ModelSide(label="reference")

    junctions = _section(inp, "JUNCTIONS")
    outfalls = _section(inp, "OUTFALLS")
    for j in junctions.values():
        side.node_inverts[str(j.name)] = float(j.elevation)
    for o in outfalls.values():
        side.node_inverts[str(o.name)] = float(o.elevation)
    side.n_junctions = len(junctions)
    side.n_outfalls = len(outfalls)
    side.n_conduits = len(_section(inp, "CONDUITS"))

    xsections = _section(inp, "XSECTIONS")
    for x in xsections.values():
        if str(x.shape).upper() == "CIRCULAR":
            side.diameters_mm.append(round(float(x.height) * 1000))
        else:
            side.n_noncircular += 1
    if not xsections:
        side.notes.append("reference [XSECTIONS] missing: diameter distribution unavailable")

    polygons = {}
    for p in _section(inp, "POLYGONS").values():
        pts = list(p.polygon)
        if len(pts) >= 3:
            polygons[str(p.subcatchment)] = Polygon(pts)
    if not polygons:
        side.notes.append("reference [POLYGONS] missing: unit geometry unavailable, "
                          "outlet agreement and service IoU are skipped")

    subcatchments = _section(inp, "SUBCATCHMENTS")
    for s in subcatchments.values():
        unit = CompareUnit(name=str(s.name), outlet=str(s.outlet), area_ha=float(s.area),
                           pct_imperv=float(s.imperviousness),
                           geometry=polygons.get(str(s.name)))
        # A zero-area subcatchment produces no runoff; in a sewer model it exists to hang
        # dry-weather loading (and a service polygon) on a node — our service-area analogue.
        (side.units_storm if unit.area_ha > 0 else side.units_sanitary).append(unit)
    if not subcatchments:
        side.notes.append("reference [SUBCATCHMENTS] missing: no land units to compare")

    n_dwf = 0
    for d in _section(inp, "DWF").values():
        if str(d.constituent).upper() == "FLOW":
            node = str(d.node)
            side.population_by_node[node] = (side.population_by_node.get(node, 0.0)
                                             + float(d.base_value))
            n_dwf += 1
    if not n_dwf:
        side.notes.append("reference [DWF] missing: sanitary-side loads unavailable")
    else:
        side.meta["population_source"] = "[DWF] FLOW base values"

    options = _section(inp, "OPTIONS")
    flow_units = str(options.get("FLOW_UNITS", "")) if hasattr(options, "get") else ""
    if flow_units:
        side.meta["flow_units"] = flow_units
    if flow_units in {"CFS", "GPM", "MGD"}:
        side.notes.append(f"reference uses US flow units ({flow_units}): areas/diameters "
                          "are NOT converted — treat absolute comparisons with care")
    for extra in ("STORAGE", "PUMPS", "WEIRS", "ORIFICES"):
        n = len(_section(inp, extra))
        if n:
            side.notes.append(f"reference has {n} [{extra}] element(s): not compared")
    return side


def read_build(path, to_crs: Optional[str] = None) -> ModelSide:
    """Load our side from a result-package root (``datastore/`` inside) or a bare datastore
    directory. ``to_crs`` is the reference model's plane; our EPSG:4326 polygons are
    transformed into it so the geometric metrics compare like with like."""
    root = Path(path)
    if (root / DATASTORE_JSON).is_file():
        ds_dir, layout = root, "datastore"
    elif (root / result_package.DATASTORE_DIR / DATASTORE_JSON).is_file():
        ds_dir, layout = root / result_package.DATASTORE_DIR, "result_package"
    else:
        raise FileNotFoundError(
            f"{root} is neither a datastore directory nor a result-package root "
            f"(no {DATASTORE_JSON} found)")
    ds = read_datastore(ds_dir)
    side = ModelSide(label="ours", meta={"layout": layout})

    if to_crs:
        project = Transformer.from_crs("EPSG:4326", to_crs, always_xy=True).transform
        side.meta["geometry_precision"] = ("float32 grid (swmm-api parses [POLYGONS] at "
                                           "f4): sub-metre differences are below the "
                                           "noise floor")
    else:
        project = None
        side.notes.append("our unit geometry not compared: reference CRS unknown "
                          "(pass --inp-crs to enable outlet agreement and service IoU)")

    def _geom(polygon, holes):
        if polygon is None or project is None:
            return None
        g = _shp_transform(project, Polygon(polygon, holes or None))
        # swmm-api reads [POLYGONS] as float32, which at projected magnitudes quantises
        # vertices by up to ~0.5 m. Snapping our side onto the SAME grid re-aligns
        # vertices the two models took from one municipal source, so shared boundaries
        # score IoU 1.0 instead of carrying the parser's noise into every metric.
        return _shp_transform(_f32grid, g)

    for j in ds.network.junctions:
        side.node_inverts[str(j.name)] = float(j.invert_m)
    for o in ds.network.outfalls:
        side.node_inverts[str(o.name)] = float(o.invert_m)
    side.n_junctions = len(ds.network.junctions)
    side.n_outfalls = len(ds.network.outfalls)
    side.n_conduits = len(ds.network.conduits)
    for c in ds.network.conduits:
        if str(c.shape).upper() == "CIRCULAR":
            side.diameters_mm.append(round(float(c.diameter_m) * 1000))
        else:
            side.n_noncircular += 1

    side.units_storm = [
        CompareUnit(name=s.name, outlet=str(s.outlet_node), area_ha=float(s.area_ha),
                    pct_imperv=float(s.pct_imperv), geometry=_geom(s.polygon, s.holes))
        for s in ds.subcatchments]
    side.units_sanitary = [
        CompareUnit(name=a.name, outlet=str(a.node), area_ha=float(a.area_ha),
                    geometry=_geom(a.polygon, a.holes))
        for a in ds.service_areas]
    for a in ds.service_areas:
        if a.population is not None:
            node = str(a.node)
            side.population_by_node[node] = (side.population_by_node.get(node, 0.0)
                                             + float(a.population))
    if ds.service_areas and not side.population_by_node:
        side.notes.append("our service areas carry no population: sanitary load "
                          "comparison unavailable")
    return side


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #
def _canon_first(raw: Dict[str, float]) -> Dict[str, float]:
    """Canonicalise point-value keys (inverts). First name wins on a collision."""
    out: Dict[str, float] = {}
    for name, v in raw.items():
        out.setdefault(canon_node(name), float(v))
    return out


def _canon_sum(raw: Dict[str, float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for name, v in raw.items():
        key = canon_node(name)
        out[key] = out.get(key, 0.0) + float(v)
    return out


def _diameter_table(ref: ModelSide, ours: ModelSide) -> List[dict]:
    def count(vals):
        c: Dict[int, int] = {}
        for v in vals:
            c[v] = c.get(v, 0) + 1
        return c
    r, o = count(ref.diameters_mm), count(ours.diameters_mm)
    return [{"mm": mm, "reference": r.get(mm, 0), "ours": o.get(mm, 0)}
            for mm in sorted(set(r) | set(o))]


def _system_section(ref_units, our_units, loads: Dict[str, dict], degraded: List[str],
                    system: str) -> dict:
    agreement = outlet_agreement(ref_units, our_units)
    iou = service_iou(ref_units, our_units)
    for name, block in (("outlet agreement", agreement), ("service IoU", iou)):
        if block.get("skipped"):
            degraded.append(f"{system} {name} skipped: {block['skipped']}")
    return {
        "partition": {"reference": partition_stats(ref_units),
                      "ours": partition_stats(our_units)},
        "outlet_agreement": agreement,
        "node_loads": loads,
        "service_iou": iou,
    }


def compare(ref: ModelSide, ours: ModelSide) -> dict:
    """Assemble the full comparison + reproduction report from the two loaded sides."""
    degraded = list(ref.notes) + list(ours.notes)

    storm_loads = {
        "area_ha": load_bias(aggregate_by_node(ref.units_storm),
                             aggregate_by_node(ours.units_storm)),
        "impervious_area_ha": load_bias(
            aggregate_by_node(ref.units_storm, lambda u: u.area_ha * u.pct_imperv / 100.0),
            aggregate_by_node(ours.units_storm, lambda u: u.area_ha * u.pct_imperv / 100.0)),
    }
    ref_pop, our_pop = _canon_sum(ref.population_by_node), _canon_sum(ours.population_by_node)
    if ref_pop and our_pop:
        population = load_bias(ref_pop, our_pop)
    else:
        side = "reference" if not ref_pop else "ours"
        population = {"skipped": f"no population loads on {side} side"}
        degraded.append(f"sanitary population comparison skipped: {population['skipped']}")

    return {
        "meta": {"reference": ref.meta, "ours": ours.meta},
        "network": {
            "counts": {
                "junctions": {"reference": ref.n_junctions, "ours": ours.n_junctions},
                "outfalls": {"reference": ref.n_outfalls, "ours": ours.n_outfalls},
                "conduits": {"reference": ref.n_conduits, "ours": ours.n_conduits},
                "noncircular_conduits": {"reference": ref.n_noncircular,
                                         "ours": ours.n_noncircular},
            },
            "invert_diff_m": load_bias(_canon_first(ref.node_inverts),
                                       _canon_first(ours.node_inverts)),
            "diameters_mm": _diameter_table(ref, ours),
        },
        "storm": _system_section(ref.units_storm, ours.units_storm, storm_loads,
                                 degraded, "storm"),
        "sanitary": _system_section(ref.units_sanitary, ours.units_sanitary,
                                    {"population": population}, degraded, "sanitary"),
        "degraded": degraded,
    }


# --------------------------------------------------------------------------- #
# human-readable rendering: every metric as a number, never a verdict
# --------------------------------------------------------------------------- #
def _f(v, spec=".3f") -> str:
    return "—" if v is None else format(v, spec)


def _bias_line(b: dict) -> str:
    if b.get("skipped"):
        return f"skipped ({b['skipped']})"
    d = b["diff"]
    return (f"n={b['n_common']} r={_f(b['pearson_r'])} "
            f"diff mean={_f(d['mean'])} median={_f(d['median'])} "
            f"p10={_f(d['p10'])} p90={_f(d['p90'])} ratio_median={_f(b['ratio_median'])}")


def _partition_line(p: dict) -> str:
    mix = " ".join(f"{k}:{v:.3f}" for k, v in p["imperv_decade_share"].items())
    return (f"n={p['n']} total={_f(p['total_area_ha'])} ha "
            f"median={_f(p['median_area_ha'])} ha  imperv share {mix or '—'}")


def _system_text(name: str, s: dict, load_labels: Dict[str, str]) -> List[str]:
    out = [f"{name} side"]
    out.append(f"  partition reference  {_partition_line(s['partition']['reference'])}")
    out.append(f"  partition ours       {_partition_line(s['partition']['ours'])}")
    a = s["outlet_agreement"]
    if a.get("skipped"):
        out.append(f"  outlet agreement     skipped ({a['skipped']})")
    else:
        out.append(f"  outlet agreement     rate={_f(a['rate'], '.2f')} "
                   f"({a['n_agree']}/{a['n_matched']} matched units agree, "
                   f"{a['n_unmatched']} unmatched of {a['n_ref_units']} reference units)")
    for key, label in load_labels.items():
        out.append(f"  {label:20} {_bias_line(s['node_loads'][key])}")
    i = s["service_iou"]
    if i.get("skipped"):
        out.append(f"  service IoU          skipped ({i['skipped']})")
    else:
        st = i["stats"]
        out.append(f"  service IoU          n_nodes={i['n_nodes_common']} "
                   f"mean={_f(st['mean'])} median={_f(st['median'])} "
                   f"p10={_f(st['p10'])} p90={_f(st['p90'])} "
                   f"(reference-only {i['n_ref_only']}, ours-only {i['n_ours_only']})")
    return out


def render_text(report: dict) -> str:
    lines = ["reference model comparison / reproduction"]
    if report.get("reference_inp"):
        lines.append(f"  reference: {report['reference_inp']}")
    if report.get("build"):
        lines.append(f"  build:     {report['build']}")
    for side, meta in report.get("meta", {}).items():
        if meta:
            lines.append(f"  {side} meta: " + "; ".join(f"{k}={v}" for k, v in meta.items()))
    lines.append("")

    net = report["network"]
    lines.append("network")
    for what, c in net["counts"].items():
        lines.append(f"  {what:20} reference {c['reference']:5d}   ours {c['ours']:5d}")
    lines.append(f"  invert diff (m)      {_bias_line(net['invert_diff_m'])}")
    dia = "  ".join(f"{r['mm']}mm ref:{r['reference']} ours:{r['ours']}"
                    for r in net["diameters_mm"])
    lines.append(f"  diameters            {dia or '—'}")
    lines.append("")

    lines += _system_text("storm", report["storm"],
                          {"area_ha": "node area (ha)",
                           "impervious_area_ha": "node imperv area"})
    lines.append("")
    lines += _system_text("sanitary", report["sanitary"], {"population": "node population"})
    lines.append("")

    degraded = report.get("degraded") or []
    lines.append("degraded: " + ("none" if not degraded else ""))
    lines += [f"  - {n}" for n in degraded]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# thin CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("reference_inp", help="the reference model .inp (confidential local "
                                          "file — read only, never copied)")
    ap.add_argument("build", help="our build: result-package root or datastore directory")
    ap.add_argument("--inp-crs", default=None, metavar="EPSG:XXXXX",
                    help="projected CRS of the .inp coordinates; required for the "
                         "geometric metrics (outlet agreement, service IoU)")
    ap.add_argument("--json", default=None, metavar="PATH",
                    help="also write the full structured report to this file")
    args = ap.parse_args(argv)

    ref = read_reference_model(args.reference_inp)
    ours = read_build(args.build, to_crs=args.inp_crs)
    report = {"reference_inp": str(args.reference_inp), "build": str(args.build),
              "inp_crs": args.inp_crs, **compare(ref, ours)}
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
    print(render_text(report), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
