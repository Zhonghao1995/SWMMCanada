# Model assumptions — what's real, what's derived, what's approximated

SWMMCanada builds a complete, runnable model fast. **Most of that model is grounded in real
data** — either measured and used as-is, or computed from measurements by standard, accepted
methods (the way professional hydrological models are built). A few parts are approximations
where direct data is thin. This page is the honest, layer-by-layer breakdown so you know exactly
which is which.

Sources (providers, endpoints, licences) are in **[DATA.md](DATA.md)**; the calibration caveat is
in the [README](README.md).

## The buckets

| | Bucket | What it means | Layers |
|---|---|---|---|
| 🟢 | **Real data** | measured & published, used as-is | storm pipe network (the 8 real-network cities); ground elevation; rainfall & temperature; parcel & building footprints; node / outfall / catch-basin locations |
| 🟢 | **Derived from real data** | computed from the above by a standard, accepted method — trustworthy model inputs, the way professional models are built | imperviousness %, terrain slope, curve number (CN), evaporation, and the outlines of parcel-followed subcatchments |
| 🟠 | **Approximated / assumed** | where direct data is thin: a sensible approximation or a standard default — apply judgment | the network **outside** the 8 cities (synthesized from streets); how subcatchments are **partitioned** (nearest-inlet service areas, not surveyed watersheds); gap-fills for missing inverts/diameters; non-circular pipes treated as circular; default roughness / depths |

> In a 7-city model, the great majority of what matters — pipes, terrain, climate, roofs, and the
> parameters derived from them — is 🟢. The 🟠 items are normal modelling approximations to be
> aware of, not red flags.

## By model layer

| Layer | Grounding | Notes |
|---|---|---|
| **Storm network** (pipes, nodes, outfalls) | 🟢 Real (8 cities) · 🟠 synthesized elsewhere | Real = published inverts, diameters, materials, locations. Honest gap-fills: ~7–10% of missing inverts are slope/neighbour-interpolated; dangling node refs snap to pipe geometry; non-circular profiles → equivalent circular (original shape kept in diagnostics). |
| **Imperviousness (%)** | 🟢 Derived | From real building roofs + road right-of-way where parcels/buildings are published; otherwise from the NALCMS land-cover raster (30 m). |
| **Terrain slope** | 🟢 Derived | Computed from the real NRCan MRDEM (30 m). |
| **Infiltration / curve number** | 🟢 Derived · 🟠 fallback | From real soil (SoilGrids/HYSOGs) → hydrologic soil group → SCS curve number. Falls back to a documented HSG-B default only if soil can't be fetched. |
| **Rainfall / temperature** | 🟢 Real | Nearest active ECCC climate station over your dates. |
| **Evaporation** | 🟢 Derived | Hargreaves (FAO-56) from the station's daily min/max/mean temperature. |
| **Subcatchment outlines** | 🟢 Derived (parcels) · 🟠 otherwise | Shapes follow **real lot lines** where a city publishes parcels (Victoria/Calgary/Surrey/London/Kelowna); a geometric catch-basin tessellation where it doesn't (Ottawa/Kitchener). |
| **Subcatchment partitioning** | 🟠 Approximated | Which area drains to which inlet is a **nearest-inlet service area, not a surveyed (DEM-derived) watershed.** This is the model's main approximation. |
| **Other parameters** (Manning's n, depression storage, default node depth) | 🟠 Assumed | Standard engineering defaults / material lookup tables; a 2 m default manhole depth where a real elevation is missing. |

## Per-city differences

All 8 real-network cities use **real pipes** (🟢). They differ only in how subcatchments and
imperviousness are built, depending on what each city publishes:

| City | Network topology | Subcatchment outline | Imperviousness |
|---|---|---|---|
| Victoria, BC | explicit node IDs | 🟢 real parcel lines | 🟢 real buildings |
| Ottawa, ON | geometry-inferred | 🟠 catch-basin tessellation | 🟢 land cover (no parcels published) |
| Calgary, AB | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Surrey, BC | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| London, ON | explicit node IDs | 🟢 real parcel lines | 🟢 real buildings |
| Kitchener–Waterloo, ON | explicit node IDs | 🟠 catch-basin tessellation | 🟢 land cover (no parcels published) |
| Kelowna, BC | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Regina, SK | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |

Outside these cities, the network itself is 🟠 synthesized from OpenStreetMap streets.

## The bottom line

> [!NOTE]
> A generated model is **grounded in real data and ready to run**: the pipes (in the 8 cities),
> terrain, climate, roofs/parcels, and the parameters derived from them are real or standard
> derivations from real data. The approximations to keep in mind are the **subcatchment
> partitioning** and, outside the 8 cities, the **network** itself.

> [!WARNING]
> **Models are uncalibrated.** No parameters are fitted to observations — this is true of any
> auto-built model, however real its inputs. Calibrate against gauged flow (e.g. ECCC HYDAT)
> before using results for design or decisions.
