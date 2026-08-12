"""Build a small AOI in several cities and print what the delineation actually produced.

    backend/.venv/bin/python backend/scripts/delineation_check.py
    backend/.venv/bin/python backend/scripts/delineation_check.py victoria ottawa

The point is the last two columns. Coverage and area conservation are WARNING-severity
checks: when they fail the build still finishes and ships a package, so a defect that loses
land shows up only in validation.json, which nobody opens. One shipped that way — an
official-basin clip deleting the far half of every straddling cell, 6.1 ha gone from one
downtown, both checks failing for as long as it took to notice. Printing them beside the
cell statistics is the cheap way to keep that visible.

Coverage is measured against the EFFECTIVE AOI (AOI ∩ served − water), the same yardstick
the validator uses: open water and land outside the street service corridor are legitimately
uncovered, and comparing against the raw AOI reads those as defects.

Live data. Cities go down; a row saying so is a result, not a failure of this script.
"""
import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from swmmcanada.datastore import read_datastore
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import build_city

#: Small AOIs — enough network to be representative, small enough to run a fleet in minutes.
SAMPLES = {
    "victoria": (-123.372, 48.421, -123.360, 48.430),
    "ottawa": (-75.700, 45.410, -75.685, 45.422),
    "regina": (-104.620, 50.445, -104.605, 50.457),
    "surrey": (-122.845, 49.180, -122.830, 49.192),
    "kelowna": (-119.500, 49.880, -119.485, 49.892),
    "london": (-81.250, 42.980, -81.235, 42.992),
}

WATCH = ("aoi_coverage", "area_conservation")


def _aoi(b):
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]})


def check(city, bbox):
    with tempfile.TemporaryDirectory() as d:
        build_city(city, _aoi(bbox), date(2023, 6, 1), date(2023, 6, 3), Path(d))
        subs = read_datastore(Path(d) / "datastore").subcatchments
        report = json.loads((Path(d) / "validation.json").read_text())
    areas = sorted(s.area_ha for s in subs if s.area_ha)
    watched = {c["id"]: c for c in report["checks"] if c["id"] in WATCH}
    return {
        "n": len(subs),
        "median_ha": areas[len(areas) // 2] if areas else 0.0,
        "uncovered": watched.get("aoi_coverage", {}).get("metrics", {}).get(
            "uncovered_fraction", float("nan")),
        "conservation": watched.get("area_conservation", {}).get("metrics", {}).get(
            "fraction", float("nan")),
        "failing": [c["id"] for c in report["checks"] if not c["passed"]],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cities", nargs="*", default=None,
                    help=f"defaults to all of: {', '.join(SAMPLES)}")
    args = ap.parse_args(argv)
    cities = args.cities or list(SAMPLES)

    unknown = [c for c in cities if c not in SAMPLES]
    if unknown:
        ap.error(f"no sample AOI for {unknown}; known: {', '.join(SAMPLES)}")

    print(f"{'city':10} {'cells':>6} {'median ha':>10} {'uncovered':>10} "
          f"{'|Σ−AOI|':>9}  failing checks")
    worst = 0
    for city in cities:
        try:
            r = check(city, SAMPLES[city])
        except Exception as e:                       # a portal being down is a row, not a stop
            print(f"{city:10} {'—':>6} {'—':>10} {'—':>10} {'—':>9}  "
                  f"unavailable: {type(e).__name__}: {str(e)[:60]}")
            continue
        worst = max(worst, len(r["failing"]))
        print(f"{city:10} {r['n']:6d} {r['median_ha']:10.3f} {r['uncovered']*100:9.1f}% "
              f"{r['conservation']*100:8.1f}%  "
              f"{', '.join(r['failing']) if r['failing'] else 'none'}")
    return 1 if worst else 0


if __name__ == "__main__":
    sys.exit(main())
