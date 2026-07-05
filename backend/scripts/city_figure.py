"""Eight-city network figure (publication style, Arial) from city_table.py's archives.

Reads each city's build workspace under --runs (the city_table.py --out directory),
plots the STORM network of all eight cities in a 2x4 panel grid — conduits, junctions,
outfalls — each panel in the city's own metric CRS with a scale bar, and writes
eight_city_networks.png (300 dpi) + .pdf (TrueType-embedded, editable text) to --out.

  backend/.venv/bin/python backend/scripts/city_figure.py \
      --runs docs/paper/city-table --out docs/paper/city-table/figures
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pyproj import Transformer

from swmmcanada.sources.cities.registry import city_spec

plt.rcParams.update({
    "font.family": "Arial",
    "font.size": 8,
    "pdf.fonttype": 42,       # embed TrueType so the PDF text stays editable Arial
    "ps.fonttype": 42,
    "savefig.dpi": 300,
})

ORDER = ["victoria", "ottawa", "london", "kitchener", "calgary", "surrey", "kelowna", "regina"]
CONDUIT = dict(color="#37474f", linewidth=0.55, alpha=0.9, zorder=2)
JUNCTION = dict(s=0.9, color="#1f77b4", linewidths=0, zorder=3)
OUTFALL = dict(s=26, color="#d32f2f", marker="v", edgecolors="white", linewidths=0.4, zorder=4)


def _storm_features(ws: Path):
    gj = json.loads((ws / "preview" / "network.geojson").read_text())
    conduits, junctions, outfalls = [], [], []
    for f in gj.get("features", []):
        p, g = f["properties"], f.get("geometry") or {}
        kind, system = p.get("kind"), p.get("system", "storm_minor")
        name = str(p.get("id", ""))
        if kind == "conduit" and system != "sanitary":
            conduits.append(g["coordinates"])
        elif kind == "junction" and system != "sanitary":
            junctions.append(g["coordinates"])
        elif kind == "outfall" and not name.startswith("SAN_"):
            outfalls.append(g["coordinates"])
    return conduits, junctions, outfalls


def _scale_bar(ax, length_m=500):
    """Draw the bar in a reserved band BELOW the network extent so it never
    overlaps geometry."""
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    span = y1 - y0
    ax.set_ylim(y0 - 0.14 * span, y1)          # reserve the band
    x = x0 + 0.04 * (x1 - x0)
    y = y0 - 0.10 * span
    ax.plot([x, x + length_m], [y, y], color="black", linewidth=1.4,
            solid_capstyle="butt", zorder=5)
    ax.text(x + length_m / 2, y + 0.018 * span, f"{length_m} m",
            ha="center", va="bottom", fontsize=6.5)


def _panel(ax, key, ws: Path):
    spec = city_spec(key)
    tr = Transformer.from_crs("EPSG:4326", spec.sub_crs, always_xy=True)
    conduits, junctions, outfalls = _storm_features(ws)

    for line in conduits:
        xs, ys = zip(*[tr.transform(x, y) for x, y in line])
        ax.plot(xs, ys, **CONDUIT)
    if junctions:
        xs, ys = zip(*[tr.transform(x, y) for x, y in junctions])
        ax.scatter(xs, ys, **JUNCTION)
    if outfalls:
        xs, ys = zip(*[tr.transform(x, y) for x, y in outfalls])
        ax.scatter(xs, ys, **OUTFALL)

    letter = chr(ord("a") + ORDER.index(key))
    ax.set_title(f"({letter}) {spec.label}\n{len(junctions)} junctions · {len(conduits)} conduits · "
                 f"{len(outfalls)} outfalls", fontsize=8, pad=3)
    ax.set_aspect("equal")
    ax.set_axis_off()
    _scale_bar(ax)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", default="docs/paper/city-table")
    ap.add_argument("--out", default="docs/paper/city-table/figures")
    args = ap.parse_args()
    runs, out = Path(args.runs), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    have = [k for k in ORDER if (runs / k / "preview" / "network.geojson").exists()]
    missing = [k for k in ORDER if k not in have]
    if missing:
        print(f"note: no build workspace for {', '.join(missing)} — panels skipped")

    ncols = 4
    nrows = (len(have) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(13, 3.4 * nrows))
    axes = axes.ravel()
    for ax, key in zip(axes, have):
        _panel(ax, key, runs / key)
    for ax in axes[len(have):]:
        ax.set_visible(False)

    fig.tight_layout(w_pad=1.2, h_pad=1.6)
    for ext in ("png", "pdf"):
        path = out / f"eight_city_networks.{ext}"
        fig.savefig(path, bbox_inches="tight")
        print("wrote", path)


if __name__ == "__main__":
    main()
