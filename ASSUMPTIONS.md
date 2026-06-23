# Model assumptions — what's real, what's derived, what's assumed

SWMMCanada gets you a complete, runnable model fast. But every model is a **mix** of measured
data, data-derived estimates, and engineering assumptions — and which is which matters a lot when
you decide how far to trust a result. This page is the honest breakdown.

For *where* each dataset comes from (providers, endpoints, licences) see **[DATA.md](DATA.md)**.
For the headline caveat — **models are not calibrated** — see the warning in the
[README](README.md).

## The three buckets

| | Bucket | What it means | Layers |
|---|---|---|---|
| 🟢 | **Real (taken as published)** | copied from an authoritative source, not altered | storm pipe network (the 7 real-network cities); ground elevation; rainfall & temperature; parcel & building footprints; catch-basin / manhole / outfall locations |
| 🟡 | **Derived from real data** | computed from real inputs with a documented method — not invented, but estimated | imperviousness %, terrain slope, curve number (CN), evaporation, and the *shape* of parcel-followed subcatchments |
| 🔴 | **Synthesized or assumed** | produced by the tool where no data exists, or a standard default | the **whole network outside the 7 cities** (from street maps); **subcatchment partitioning**; gap-fills for missing inverts/diameters; non-circular pipes treated as circular; default roughness / depths / parameters |

## By model layer

| Layer | Real / Derived / Synthesized | Notes |
|---|---|---|
| **Storm network** (pipes, nodes, outfalls) | 🟢 Real for the **7 cities**; 🔴 Synthesized elsewhere | Real = published inverts, diameters, materials, locations. Honest gap-fills: ~7–10% of missing inverts are slope/neighbour-interpolated, dangling node refs snap to pipe geometry, and non-circular profiles are approximated as an equivalent **circular** pipe (original shape kept in diagnostics). |
| **Subcatchment boundaries** | 🟡 / 🔴 — **always an approximation** | These are **nearest-inlet "service areas," not surveyed (DEM-derived) watersheds.** Where a city publishes parcels they follow real lot lines (🟡); otherwise they are a geometric tessellation around catch basins/nodes (🔴). This is the single biggest approximation in the model. |
| **Imperviousness (%)** | 🟡 Derived | From **real building roofs + road right-of-way** where parcels/buildings are published; otherwise from the **NALCMS** land-cover raster (30 m pixels). |
| **Terrain slope** | 🟡 Derived | Computed from the **real NRCan MRDEM** (30 m) elevation. |
| **Infiltration / curve number** | 🟡 Derived (🔴 fallback) | From **real soil** (SoilGrids/HYSOGs) → hydrologic soil group → SCS curve number. Falls back to a documented **HSG-B** default if soil data can't be fetched. |
| **Rainfall / temperature** | 🟢 Real | Nearest active **ECCC** climate station over your chosen dates. |
| **Evaporation** | 🟡 Derived | Hargreaves (FAO-56) from the station's daily min/max/mean temperature. |
| **Other parameters** (Manning's n, depression storage, default node depth) | 🔴 Assumed | Standard engineering defaults / material lookup tables; a 2 m default manhole depth is used where a real elevation is missing. |

## Per-city differences

All 7 real-network cities use **real pipes**. They differ in how the subcatchments and
imperviousness are built, depending on what each city publishes:

| City | Network topology | Subcatchment shape | Imperviousness |
|---|---|---|---|
| Victoria, BC | explicit node IDs | 🟡 real parcel lines | 🟡 real buildings |
| Ottawa, ON | geometry-inferred | 🔴 catch-basin tessellation | 🟡 land cover (no parcels published) |
| Calgary, AB | geometry-inferred | 🟡 real parcel lines | 🟡 real buildings |
| Surrey, BC | geometry-inferred | 🟡 real parcel lines | 🟡 real buildings |
| London, ON | explicit node IDs | 🟡 real parcel lines | 🟡 real buildings |
| Kitchener–Waterloo, ON | explicit node IDs | 🔴 catch-basin tessellation | 🟡 land cover (no parcels published) |
| Kelowna, BC | geometry-inferred | 🟡 real parcel lines | 🟡 real buildings |

Everywhere **outside** these cities: the network itself is 🔴 synthesized from OpenStreetMap
streets — a plausible layout, not the real pipes.

## The bottom line

> [!WARNING]
> A generated model is a **structurally sound, runnable starting point — not validated truth.**
> The pipe network (in the 7 cities), terrain, climate, and building/parcel footprints are real;
> imperviousness, slope and CN are estimated from real data; the **subcatchment partitioning is an
> approximation** and, outside the 7 cities, so is the network. Because of this — and because no
> parameters are fitted to observations — **models are uncalibrated.** Calibrate against gauged
> flow (e.g. ECCC HYDAT) before using any result for design or decisions.
