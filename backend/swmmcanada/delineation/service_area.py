"""Sewer service areas from published infrastructure (ADR 0029 Q1, ADR 0031).

Which land's wastewater reaches which sanitary node. **Not** a watershed: sewage travels by
lateral, so the boundary follows parcels and connections and may cross a topographic divide
without anything being wrong.

*One pipeline, different inputs* (ADR 0029 Q10) taken literally — this reuses the same
shaping and outlet-resolution seams the storm path uses, and changes only what feeds them::

    storm     seeds = catch basins   outlet = nearest storm conduit endpoint
    sanitary  seeds = sanitary manholes / lateral connection points
              outlet = nearest sanitary conduit endpoint

There is no second algorithm here, and there must not be one: the difference between a
surface catchment and a service area is which network you hand it, not which code you run.

Phase 0 (2026-08-12) found 16 supported cities publishing sanitary laterals and 12 with both
laterals and parcels, so this path has real subjects — unlike the authoritative-polygon path,
which had none and is not built.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from swmmcanada.build.models import NetworkIn, SewerServiceArea

#: A lateral endpoint further than this from any sanitary node is not a connection to it.
#: Service leads run from the lot line to the main in the street; beyond a block width the
#: pairing is a guess, and a guessed connection routes a household to the wrong sewer.
MAX_LATERAL_SNAP_M = 60.0


def _lateral_endpoints(laterals) -> List[Tuple[float, float]]:
    """Both ends of every lateral. Which end is the main and which is the property varies by
    city and by digitising direction, so both are offered to the snapper and the network
    decides — inferring direction from geometry we have not verified would be a guess."""
    out = []
    for f in laterals or []:
        g = f.get("geometry") or {}
        c = g.get("coordinates") or []
        if g.get("type") == "MultiLineString":
            c = [p for part in c for p in part]
        if len(c) >= 2:
            out.append((float(c[0][0]), float(c[0][1])))
            out.append((float(c[-1][0]), float(c[-1][1])))
    return out


def _seeds_from(network: NetworkIn, laterals, crs: str) -> Tuple[Dict, str]:
    """Seed points for the shaping step, best evidence first.

    Laterals are preferred because they are the actual connection: a lateral endpoint says
    *this* property drains to *this* main. Manholes are the fallback — they say only where
    the network is, which is a weaker claim and is recorded as such.
    """
    from swmmcanada.geo.crs import lonlat_projector

    ends = _lateral_endpoints(laterals)
    if ends:
        to_m = lonlat_projector(crs)
        nodes = [(n.name, to_m(n.x, n.y)) for n in network.junctions]
        if nodes:
            seeds, kept = {}, 0
            for i, (lon, lat) in enumerate(ends):
                x, y = to_m(lon, lat)
                name, (nx, ny) = min(nodes, key=lambda n: (n[1][0] - x) ** 2 + (n[1][1] - y) ** 2)
                if ((nx - x) ** 2 + (ny - y) ** 2) ** 0.5 <= MAX_LATERAL_SNAP_M:
                    seeds[f"LAT{i}"] = (lon, lat)
                    kept += 1
            if kept >= 2:
                return seeds, "lateral"
            # Laterals exist but none snapped: a different fact from none being published,
            # and the more interesting one — it usually means the lateral layer and the
            # main layer disagree about where the network is.
            return ({j.name: (j.x, j.y) for j in network.junctions},
                    "manhole_laterals_unusable")
    return ({j.name: (j.x, j.y) for j in network.junctions}, "manhole")


def derive_service_areas(
    network: NetworkIn, parcels, aoi, *, laterals=None, crs: str = "EPSG:32610",
    system: str = "sanitary", buildings=None,
) -> Tuple[List[SewerServiceArea], Dict]:
    """Service areas for one sanitary/combined network. Returns ``(areas, diagnostics)``.

    ``network`` must already be the single-system subgraph — pass a filtered network, not
    the whole model, or storm nodes will collect wastewater.
    """
    from swmmcanada.sources.cities import base

    if len(network.junctions) < 2:
        return [], {"reason": "sanitary network too small to serve areas",
                    "n_junctions": len(network.junctions)}

    seeds, seed_source = _seeds_from(network, laterals, crs)
    if len(seeds) < 2:
        return [], {"reason": "no usable seeds", "seed_source": seed_source}

    parcels, n_remainder = base._drop_remainder_donuts(parcels)
    cells, shape_method, n_dropped = base._shape_cells(seeds, parcels, aoi, crs)
    outlet_of = base._outlet_resolver(network, crs)

    dwellings = _dwellings_per_cell(cells, buildings, crs) if buildings else {}

    areas: List[SewerServiceArea] = []
    for seed_id, i, poly_m, exterior, holes in cells:
        name = f"SSA_{seed_id}" if i == 0 else f"SSA_{seed_id}__{i + 1}"
        areas.append(SewerServiceArea(
            name=name, node=outlet_of(seeds[seed_id]), area_ha=poly_m.area / 1e4,
            system=system, polygon=exterior, holes=holes or None,
            dwelling_units=dwellings.get(name),
            geometry_source="derived"))

    return areas, {
        "method": f"{seed_source}-seeded, {shape_method}-shaped",
        "seed_source": seed_source, "shape_method": shape_method,
        "n_seeds": len(seeds), "n_service_areas": len(areas),
        "n_dropped_invalid": n_dropped, "n_remainder_donuts": n_remainder,
        "n_with_dwelling_counts": sum(1 for a in areas if a.dwelling_units),
        # Named so a reader can tell a connection-backed boundary from a proximity guess.
        "evidence": {
            "lateral": "lateral connections (a lateral says which property feeds which main)",
            "manhole": "manhole proximity (no laterals published)",
            "manhole_laterals_unusable": (
                f"manhole proximity (laterals published but none within "
                f"{MAX_LATERAL_SNAP_M:.0f} m of a node — lateral and main layers disagree "
                f"about where the network is)"),
        }[seed_source],
    }


def _dwellings_per_cell(cells, buildings, crs: str) -> Dict[str, int]:
    """Buildings whose representative point falls in each cell — the dwelling-count rung of
    the population ladder (ADR 0031). A representative point, not a centroid: a centroid can
    fall outside an L-shaped footprint and land the building in a neighbour's area."""
    import geopandas as gpd
    from shapely.geometry import shape

    geoms = [shape(f["geometry"]) for f in (buildings or [])
             if (f.get("geometry") or {}).get("coordinates")]
    if not geoms:
        return {}
    pts = gpd.GeoSeries(geoms, crs="EPSG:4326").to_crs(crs).representative_point()
    sidx = pts.sindex
    out: Dict[str, int] = {}
    for seed_id, i, poly_m, _exterior, _holes in cells:
        name = f"SSA_{seed_id}" if i == 0 else f"SSA_{seed_id}__{i + 1}"
        hits = list(sidx.query(poly_m, predicate="within"))
        if hits:
            out[name] = len(hits)
    return out
