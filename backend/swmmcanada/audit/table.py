"""Capability table assembly (ADR 0030) — scan rows in, graded capability rows out.

    python -m swmmcanada.audit.table --rows docs/reports/capability/rows.json \
                                     --out  docs/reports/capability/table.json

The one thing this module does that ``measure.judge`` cannot do alone: it counts anchors
**inside the polygon layer's own coverage** instead of city-wide. ADR 0030 makes that a
correctness requirement rather than a refinement, and Hamilton is the proof — its 8,147
combined catchments cover the old combined district only, so the city-wide denominator
dilutes a per-segment layer by 2.3x (ratio 0.167 city-wide vs 0.389 within coverage).

The envelope used for scoping is coarser than the true footprint, so every coverage-scoped
ratio here is a **floor**. That direction is deliberate: it can only under-state how fine a
layer is, never over-state it, so no layer is promoted on the strength of a loose bound.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from swmmcanada.audit.measure import Level, judge
from swmmcanada.audit.scanner import Catalogue
from swmmcanada.sources.cities.capability import ANCHOR_ROLES, Role, SYSTEMS

AREA_ROLES = (Role.SUBCATCHMENT, Role.CATCHMENT)


def _area_layers(rows: Sequence[dict], system: str) -> List[dict]:
    want = {r.value for r in AREA_ROLES}
    return [r for r in rows if r.get("role") in want and r.get("system") == system
            and r.get("n_features")]


def _anchor_layers(rows: Sequence[dict], system: str) -> List[dict]:
    want = {r.value for r in ANCHOR_ROLES.get(system, ())}
    return [r for r in rows if r.get("role") in want and r.get("system") == system
            and r.get("n_features")]


def scope_rows(cat: Optional[Catalogue], rows: Sequence[dict], system: str) -> List[dict]:
    """Copy of ``rows`` where each anchor layer's count is restricted to the union extent of
    this system's area layers. Falls back to the city-wide count (flagged) when no extent is
    available — a missing bound must degrade loudly, not silently pass as coverage-scoped."""
    areas = _area_layers(rows, system)
    anchors = _anchor_layers(rows, system)
    if cat is None or not areas or not anchors:
        return list(rows)
    extents = [a.get("extent") for a in areas if a.get("extent")]
    if not extents:
        return list(rows)
    ext = max(extents, key=lambda e: abs((e["xmax"] - e["xmin"]) * (e["ymax"] - e["ymin"])))

    scoped, by_id = [], {(a["service"], a["layer_id"]) for a in anchors}
    for r in rows:
        if (r.get("service"), r.get("layer_id")) not in by_id:
            scoped.append(r)
            continue
        n = cat.count_within(r["service"], r["layer_id"], ext)
        row = dict(r)
        if n is None:
            row["coverage_scoped"] = False
        else:
            row["n_features_city_wide"] = r["n_features"]
            row["n_features"] = n
            row["coverage_scoped"] = True
        scoped.append(row)
    return scoped


def build(rows: Sequence[dict], cat: Optional[Catalogue] = None) -> Dict:
    by_city = defaultdict(list)
    for r in rows:
        by_city[r["city"]].append(r)

    out = []
    for city in sorted(by_city):
        city_rows = by_city[city]
        external = any(r.get("external_reference") for r in city_rows)
        for system in SYSTEMS:
            scoped = scope_rows(cat, city_rows, system)
            verdict = judge(scoped, system)
            if verdict.level is Level.NONE:
                continue
            areas = _area_layers(city_rows, system)
            entry = verdict.as_dict()
            entry.update({
                "city": city, "system": system, "external_reference": external,
                "coverage_scoped": any(r.get("coverage_scoped") for r in scoped),
                "area_layers": [{"name": a["layer_name"], "service": a["service_name"],
                                 "n": a["n_features"], "role": a["role"]} for a in areas],
            })
            out.append(entry)

    unclassified = [r for r in rows
                if (r.get("skip_reason") or "").startswith("unclassified")]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capabilities": out,
        "unclassified_layers": len(unclassified),
        "cities_scanned": len(by_city),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assemble the capability table")
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default=".audit-cache")
    ap.add_argument("--no-scope", action="store_true",
                    help="skip coverage scoping (city-wide denominators; diagnostic only)")
    args = ap.parse_args(argv)

    rows = json.loads(Path(args.rows).read_text())["rows"]
    cat = None if args.no_scope else Catalogue(Path(args.cache))
    table = build(rows, cat)
    Path(args.out).write_text(json.dumps(table, indent=2))
    print(f"wrote {args.out} — {len(table['capabilities'])} capability rows, "
          f"{table['unclassified_layers']} unclassified layers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
