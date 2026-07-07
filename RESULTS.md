# Results — real-municipal-network validation

SWMMCanada can ingest a city's **published storm-sewer open data** and turn it into a SWMM
model that runs clean in the **EPA SWMM 5.2 engine**. This page documents that, validated
end-to-end on two Canadian cities with structurally different data.

> [!IMPORTANT]
> **"Zero engine errors" means runnable, not calibrated.** Every result below confirms the
> model is *structurally sound* and executes in the EPA SWMM engine without error — it is
> **not** a claim that the hydrology is accurate. Parameters are first-pass estimates from
> open data; calibrate against observations before using any output for design or decisions.

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

### Delineation v2 — DEM basins behind a terrain honesty gate

Junction-seeded subcatchments (synthesis mode and the no-catch-basin fallback) are now
delineated from the **conditioned DEM** — depressions filled, **OSM streets burned in** so
urban flow follows roads, D8 basins to each manhole (`pyflwdir`) — but only where the terrain
earns it. A two-layer **honesty gate** decides per AOI and records its readings in
`validation.json`, with a **resolution-aware threshold**: below **4.0 %** median conditioned
slope at ≥10 m posting (MRDEM's ±1–2 m accuracy makes gentler readings noise) or below
**1.0 %** under LiDAR (≤2 m posting, ~0.1–0.2 m accuracy — urban micro-slope is real signal),
the delineation honestly stays junction-Voronoi; a DEM result that fails validation also falls
back automatically. The DEM source itself is automatic: **HRDEM LiDAR (1–2 m) wherever a
sampled read proves coverage, MRDEM-30 everywhere else** — e.g. downtown Ottawa resolves to
the city's own 2020 LiDAR (1 m), reads 2.52 % ≥ the 1.0 % fine gate, and upgrades from
Voronoi to 442 DEM basins with zero errors and 0.0 % uncovered (42 s delineation).

The 4.0 % default is measured, not guessed — median conditioned slope over the seven
downtown fixture AOIs vs two hillside AOIs (MRDEM-30, 2026-07):

| AOI | median slope % | gate (4.0 %) |
|---|---|---|
| Calgary 1.14 · Ottawa 1.26 · Surrey 1.99 · Kelowna 2.18 · London 2.51 · Victoria 2.90 · Kitchener 3.26 | 1.1–3.3 | **Voronoi** (inside DEM noise) |
| Kelowna hillside 9.12 · North-Vancouver slopes 13.25 | 9–13 | **DEM basins** |

Flat/hilly contrast (same code, gate deciding): downtown Ottawa (1.26 %) keeps the honest
Voronoi — 442 cells, zero errors, full coverage. The Kelowna hillside AOI (9.12 %) crosses the
gate: 197 street-burned DEM basins, zero errors, 0.0 % uncovered, terrain-following boundaries
instead of geometric polygons:

![Delineation v2 on a hillside AOI](assets/delineation_v2_kelowna_hillside.png)

A guarded smoke test (`tests/build/test_delineation_v2_run.py`) runs EPA SWMM 5.2 on a
v2-delineated model and asserts a clean run with continuity within tolerance — a runnability
check, deliberately not an accuracy claim (calibration stays downstream).

**Regression baselines** (`tests/validate/test_regression_baselines.py`) lock today's
delineation verdict on the checked-in downtown fixtures — Victoria: 74 junction-Voronoi
cells, Ottawa: 400 catch-basin cells (the CI-runnable subsets of the full downtown runs
above: 732 / 2,461) — asserting one cell per seed, zero errors, no blank holes or overlap,
exactly today's warning set (Ottawa's >50 m outlet-distance tail stays surfaced), determinism
(same input ⇒ identical cells), and the no-catch-basin fallback to junction-Voronoi. Any
future delineation change (DEM refinement, outlet rerouting) must move these baselines in a
reviewed diff — it cannot silently degrade coverage.

## Eight-city engine validation

All eight real-network cities, built end-to-end from live open data on one fixed week
(1–7 June 2022, ~1–2.6 km² fixed downtown AOIs, Horton infiltration default) and run in
EPA SWMM 5.2.4 — **zero engine errors and flow-routing continuity within ±1.1% in all
eight**, with **every AOI automatically served hourly rainfall** (ADR 0014 tier: nearest
station whose hourly record covers ≥90% of the period; the tier, station and coverage are
recorded in each build's manifest and validation report). One pinned script produces the
whole table: `backend/.venv/bin/python backend/scripts/city_table.py` (figure:
`backend/scripts/city_figure.py`).

![Eight built storm networks](results/eight_city_networks.png)

| City | Topology | km² | Junctions / conduits / outfalls | Subcatchments | Inverts gap-filled | Rain → runoff (mm) | Runoff / routing continuity (%) |
|---|---|--:|:--:|--:|--:|:--:|:--:|
| Victoria, BC | explicit node IDs | 1.36 | 508 / 514 / 30 | 767 | 7.5% | 11.8 → 3.5 | −0.21 / −0.23 |
| Ottawa, ON | geometry-inferred | 2.61 | 619 / 630 / 73 | 2,461 | 15.9% | 13.7 → 5.6 | −0.56 / +0.26 |
| London, ON | explicit node IDs | 0.91 | 187 / 188 / 14 | 352 | 3.6% | 18.3 → 7.2 | −0.27 / +0.16 |
| Kitchener–Waterloo, ON | explicit manhole IDs | 0.90 | 454 / 456 / 23 | 341 | 0.4% | 9.6 → 3.4 | −0.13 / −0.17 |
| Calgary, AB | geometry-inferred | 0.78 | 371 / 383 / 75 | 171 | 38.7% | 34.2 → 19.0 | −0.28 / −1.08 |
| Surrey, BC | geometry-inferred | 0.81 | 159 / 160 / 23 | 243 | 1.2% | 28.4 → 6.6 | −0.09 / −0.004 |
| Kelowna, BC | geometry-inferred | 0.80 | 144 / 145 / 20 | 161 | 9.3% | 25.2 → 9.0 | −0.13 / −0.63 |
| Regina, SK | geometry-inferred | 0.79 | 247 / 247 / 17 | 428 | 14.2% | 0.0 → 0.0¹ | 0.0 / 0.0¹ |

¹ The fixed comparison week was dry at Regina's nearest station (0 mm at both tiers), so
continuity is trivially exact; engine execution is still verified.

**Hourly forcing changed the physics, not just the numbers.** Under the previous daily
forcing, a day's rain was spread at ~0.1–0.5 mm/h — often below the continuous evaporation
demand — so runoff ratios were unrealistically low (Victoria: 3.4%). With the hourly tier
they land at **23–55%**, plausible for dense downtowns. The three infiltration methods
(ADR 0013) now also compute measurably differently — Calgary's wet week infiltrates
10.3 mm under Horton vs 7.6 mm under Curve Number — though downtown *runoff* remains
impervious-dominated at these intensities; full runoff divergence awaits design-storm
events (future dual-drainage work).

**Real intensities also exposed — and fixed — an engine-numerics gap.** Daily drizzle let
SWMM's default dynamic-wave settings pass unnoticed; hourly pulses made a handful of
short/steep conduits oscillate (Kitchener reached −976% routing continuity from ONE
oscillating node; Kelowna's earlier −20.6% single-node artefact was the same phenomenon).
The writer now sets standard municipal stabilisation numerics (5 s max routing step with
variable stepping, partial inertial damping, both normal-flow criteria, 20 trials) —
measured effect: Kitchener −976% → −0.17%, Ottawa −14.6% → +0.26%, **Kelowna −20.6% →
−0.63%**. An automatic adverse-invert data check remains open as #77.

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
from swmmcanada.pipeline import build_city

vic = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.375, 48.418], [-123.360, 48.418], [-123.360, 48.429], [-123.375, 48.429], [-123.375, 48.418]]]})
build_city("victoria", vic, date(2022, 6, 1), date(2022, 6, 7), "victoria_out/")

ott = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-75.705, 45.41], [-75.685, 45.41], [-75.685, 45.425], [-75.705, 45.425], [-75.705, 45.41]]]})
build_city("ottawa", ott, date(2022, 6, 1), date(2022, 6, 7), "ottawa_out/")
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
