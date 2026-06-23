# Results — real-municipal-network validation

SWMMCanada can ingest a city's **published storm-sewer open data** and turn it into a SWMM
model that runs clean in the **EPA SWMM 5.2 engine**. This page documents that, validated
end-to-end on two Canadian cities with structurally different data.

## Summary

| | **Victoria, BC** | **Ottawa, ON** |
|---|---|---|
| Source topology | explicit node IDs | **inferred from pipe geometry** (no node IDs) |
| AOI (downtown) | 1.36 km² | 2.6 km² |
| Junctions / conduits / outfalls | 519 / 495 / 11 | 619 / 630 / 73 |
| Subcatchments | 732 (catch-basin + parcel/building) | 2,461 (catch-basin + land cover) |
| Rainfall → surface runoff | 11.8 mm → 6.8 mm | 19.4 mm → 12.5 mm |
| **EPA SWMM 5.2 errors** | **0** | **0** |
| Runoff continuity | −0.00 % | −0.05 % |
| Flow-routing continuity | +0.06 % | −5.1 % |

Both models were built automatically from open data (real pipes + ECCC rainfall + NRCan
terrain + land cover + soil) and executed in EPA SWMM 5.2.4 with **zero errors**.

## Climate forcing — rainfall + evaporation

The `.inp` carries the full climate-forcing package, all from the **nearest usable ECCC
climate station** to the AOI:

- **Rainfall** → SWMM `[TIMESERIES]`/`[RAINGAGES]`. Station selection prefers the nearest
  station *with usable precipitation*, skipping discontinued / precip-less stations; trace
  (`'T'`) days and residual gaps resolve to 0 mm so the raingage never goes NaN.
- **Evaporation** → SWMM `[EVAPORATION] TIMESERIES`. A daily potential-evaporation series
  derived by **Hargreaves (FAO-56)** from the station's `tmin`/`tmax`/`tmean`. Sub-freezing
  days clamp to 0; days missing the diurnal range are skipped.
- **Datastore** — rainfall, temperature, and evaporation are written together to
  `forcing.nc` (CF-1.8), with the evaporation method recorded in `datastore.json` provenance.

A live central-Ottawa build (1.7 km², 1–7 Jun 2022) runs clean in EPA SWMM 5.2 with
evaporation active: **0 errors**, runoff/flow-routing continuity −0.024 % / −0.044 %,
≈4.1 mm/day mean evaporation.

## Model validation

Every generated model ships a `validation.json` (beside `model.inp`) and is checked before
the `.inp` is emitted. Checks are two-tier: an **Error** means the subcatchment model is
structurally untrustworthy and the build is **stopped**; a **Warning** means it runs but is
approximate. "Correct" means the subcatchments tile the AOI with valid geometry and route to
real outlets:

| Check | Tier (default) | What it catches |
|---|---|---|
| outlet present / exists | Error | a subcatchment with no outlet, or one not in the network |
| positive area / valid geometry | Error | zero-area or self-intersecting cells |
| AOI coverage | Warning >2%, Error >10% | **blank holes** the AOI that no subcatchment covers |
| overlap | Warning >0.5%, Error >5% | double-counted runoff area |
| AOI containment | Warning >2%, Error (cell >50% out) | cells spilling outside the drawn AOI |
| area conservation | Warning >5% | Σ cell area drifting from the AOI area |
| node coverage | Warning | network nodes in no subcatchment |
| outlet distance | Warning (>20 m / >50 m tiers) | inlets routed to an implausibly far manhole |
| shape plausibility | Warning | extreme-size or very elongated cells |

The report also records an honest delineation **method** (`catchbasin_parcel` /
`catchbasin_voronoi` / `junction_voronoi`), its physical basis (a *nearest-inlet / -node
service area*, **not** a DEM-derived watershed), and a confidence level — so an approximate
service area is never mistaken for a true hydrological catchment.

## Victoria — fidelity to the source data

Victoria publishes storm mains with explicit topology, invert elevations, diameters and
materials, so the network is a faithful copy of the city's data:

- **Node positions are an exact copy** of the source manholes/outfalls (0 m offset).
- **89 % of conduits lie within 1 m** of the real pipe centreline (95 % within 5 m); the
  small residual is the straight node-to-node representation vs. curved polylines.

![Victoria network vs. the official city map](results/victoria_fidelity.png)
*Left: City of Victoria official open-data storm map. Right: the generated SWMM network
(black = conduits, blue = junctions, red ★ = outfalls) overlaid on it — they coincide.*

Subcatchments use the **catch-basin + parcel/building** method: drainage units seeded on the
real catch basins (inlets), with imperviousness from real building footprints + road
right-of-way (far finer than a 30 m land-cover raster).

![Victoria subcatchments by imperviousness](results/victoria_subcatchments.png)
*732 catch-basin subcatchments coloured by impervious % (mean ~68 %, realistic downtown
variation), vs. the city's 14 macro catchment areas (right).*

## Ottawa — geometry-inferred topology

Ottawa publishes inverts, diameters and materials but **no node IDs**, so the topology is
reconstructed by snapping pipe endpoints to shared nodes (the same shared assembler handles
both cities). Outfalls come from the city's outlet layer plus a sink per disconnected
component so the network is always mass-balanced. Parcels/buildings aren't public there, so
subcatchments seed on real catch basins with land-cover imperviousness.

![Ottawa network vs. the official city map](results/ottawa_network.png)
*Left: City of Ottawa official open-data storm pipes. Right: the generated SWMM network
(geometry-inferred) tracing the same pipe grid.*

The −5.1 % flow-routing continuity (vs. Victoria's +0.06 %) reflects honest v1 limits:
~16 % of Ottawa inverts were gap-filled (published as 0) and manholes carry no ground
elevation, so junction depths are defaulted. It runs with 0 errors; tighter invert
propagation and real outlet matching are the next fidelity steps.

## Reproduce

```python
from datetime import date
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import build_from_victoria, build_from_ottawa

vic = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.375, 48.418], [-123.360, 48.418], [-123.360, 48.429], [-123.375, 48.429], [-123.375, 48.418]]]})
build_from_victoria(vic, date(2022, 6, 1), date(2022, 6, 7), "victoria_out/")

ott = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-75.705, 45.41], [-75.685, 45.41], [-75.685, 45.425], [-75.705, 45.425], [-75.705, 45.41]]]})
build_from_ottawa(ott, date(2022, 6, 1), date(2022, 6, 7), "ottawa_out/")
```

Each writes `model.inp` (plus the model-ready datastore); open it in EPA SWMM 5 to run it.

## Limitations (v1)

- Cross-sections are modelled as circular (the build target carries a single diameter).
- Subcatchment divides are Voronoi-seeded on real inlets — real inlets and (where available)
  building/road imperviousness, but geometric divides rather than DEM flow paths.
- Real-network ingestion is a **per-city adapter** (`backend/swmmcanada/sources/cities/`):
  each city's schema differs, so a new city is a small fetch + field mapping over the shared
  assembler. Cities that don't publish inverts fall back to synthesizing a network from open
  data.
