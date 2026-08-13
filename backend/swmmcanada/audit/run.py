"""Fleet scan orchestration (ADR 0030) — produces the capability table.

    python -m swmmcanada.audit.run --out docs/reports/capability/rows.json

Walk order per city: discover service roots from the adapter, enumerate services, keep the
ones whose name looks drainage-relevant, enumerate their layers, classify each, and measure
the ones classification recognises.

**Nothing is dropped silently.** Services and layers that the keyword filter or the
classifier declines are emitted as rows with ``role: null`` and a ``skip_reason``, so the
report can state what was seen and not measured. A scan that quietly narrowed its own scope
would read as "this city publishes nothing" — the exact false negative this audit exists to
prevent.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from swmmcanada.audit.classify import suggest
from swmmcanada.audit.discover import (EXTERNAL_REFERENCE, NON_ARCGIS, PER_SERVICE_PROXY,
                                       service_roots)
from swmmcanada.audit.scanner import Catalogue, _relevant
from swmmcanada.sources.cities.capability import (EXPECTED_GEOMETRY, Role,
                                                  apply_role_map)
from swmmcanada.sources.cities.registry import CITIES


@dataclass
class LayerRow:
    city: str
    service: str
    service_name: str
    layer_id: Optional[int]
    layer_name: str
    geometry_type: Optional[str] = None
    role: Optional[str] = None
    system: Optional[str] = None
    n_features: Optional[int] = None
    fields: List[str] = field(default_factory=list)
    extent: Optional[Dict] = None
    skip_reason: Optional[str] = None
    external_reference: bool = False


def _service_name(url: str) -> str:
    parts = url.rstrip("/").split("/")
    return parts[-2] if parts[-1] in ("MapServer", "FeatureServer") else parts[-1]


def scan_city(cat: Catalogue, city: str, roots: List[str], *, external: bool = False,
              measure: bool = True) -> List[LayerRow]:
    """Enumerate and measure one city.

    The keyword filter applies **only** to services found by walking a folder, never to a
    service the adapter itself names. An adapter referencing a service *is* the proof of
    relevance, and municipal catalogues do not name things the way an outsider would guess:
    London serves its entire storm network from ``OpenData_Environment``, which no drainage
    keyword matches. Filtering that by name cost the city 41 of its 43 layers.
    """
    rows: List[LayerRow] = []
    referenced = {r.rstrip("/") for r in roots}
    # A service is reachable both directly (the adapter names it) and via its parent folder;
    # scan it once, or every count in it is duplicated.
    seen_services: set = set()
    for root in roots:
        for svc in cat.services(root):
            if svc.rstrip("/") in seen_services:
                continue
            seen_services.add(svc.rstrip("/"))
            sname = _service_name(svc)
            if svc.rstrip("/") not in referenced and not _relevant(sname):
                # Recorded, not discarded: the fleet report must be able to say how much of
                # each catalogue was passed over and on what grounds.
                rows.append(LayerRow(city, svc, sname, None, "", external_reference=external,
                                     skip_reason="service name not drainage-relevant"))
                continue
            for lay in cat.layers(svc):
                lid, lname = lay.get("id"), lay.get("name") or ""
                role, system = apply_role_map(city, sname, lname,
                                              suggest(sname, lname))
                row = LayerRow(city, svc, sname, lid, lname,
                               geometry_type=lay.get("geometryType"),
                               role=role.value if role else None, system=system,
                               external_reference=external)
                if role is None:
                    row.skip_reason = "unclassified: no role suggestion"
                elif measure:
                    meta = cat.layer_meta(svc, lid) or {}
                    geom = meta.get("geometryType") or row.geometry_type
                    row.geometry_type = geom
                    expected = EXPECTED_GEOMETRY.get(role)
                    if expected and geom and geom not in expected:
                        # Name says one thing, geometry says another. Never resolve this
                        # silently — a lookalike layer counted as the real one corrupts an
                        # anchor denominator, and nothing downstream would reveal it.
                        row.role = None
                        row.skip_reason = (f"geometry mismatch: {role.value} expects "
                                           f"{sorted(expected)}, layer is {geom}")
                    else:
                        row.n_features = cat.count(svc, lid)
                        row.fields = [f["name"] for f in (meta.get("fields") or [])]
                        row.extent = meta.get("extent")
                rows.append(row)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phase 0 capability scan")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default=".audit-cache")
    ap.add_argument("--cities", default="", help="comma-separated subset (default: whole fleet)")
    ap.add_argument("--pause", type=float, default=0.0)
    args = ap.parse_args(argv)

    cat = Catalogue(Path(args.cache), pause=args.pause)
    wanted = {c.strip() for c in args.cities.split(",") if c.strip()}
    rows: List[LayerRow] = []
    supported = [c.key for c in CITIES]

    for city in supported:
        if wanted and city not in wanted:
            continue
        if city in NON_ARCGIS:
            rows.append(LayerRow(city, "", "", None, "",
                                 skip_reason=f"not an enumerable catalogue: {NON_ARCGIS[city]}"))
            continue
        rows.extend(scan_city(cat, city, service_roots(city)))
        print(f"{city}: {len(rows)} rows so far "
              f"({cat.stats.requests} req, {cat.stats.cache_hits} cached, "
              f"{len(cat.stats.errors)} err)", flush=True)

    for city, roots in EXTERNAL_REFERENCE.items():
        if wanted and city not in wanted:
            continue
        rows.extend(scan_city(cat, city, roots, external=True))
        print(f"{city} (external reference): {len(rows)} rows", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "rows": [asdict(r) for r in rows],
        "stats": {"requests": cat.stats.requests, "cache_hits": cat.stats.cache_hits,
                  "errors": cat.stats.errors},
        "notes": {"per_service_proxy": PER_SERVICE_PROXY, "non_arcgis": NON_ARCGIS},
    }, indent=2))
    print(f"wrote {out} — {len(rows)} rows, {len(cat.stats.errors)} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
