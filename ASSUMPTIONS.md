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
| **Snowmelt** | 🟠 Assumed parameters | On by default whenever a temperature series exists (above the 0 °C dividing temperature nothing accumulates, so summer runs are unchanged). One URBAN snow pack for all subcatchments: melt coefficients 0.1–0.3 mm·h⁻¹·°C⁻¹ (typical degree-day factors 2.4–7.2 mm·d⁻¹·°C⁻¹, converted), base 0 °C, free-water fraction 0.10, plowable fraction 0.10, 100 %-cover depth 25 mm; ATI weight 0.5 and negative-melt ratio 0.6 are SWMM defaults. **Uncalibrated first-pass values — calibrate downstream before using cold-season results.** |
| **Subcatchment outlines** | 🟢 Derived (parcels) · 🟠 otherwise | Shapes follow **real lot lines** where a city publishes parcels (Victoria/Calgary/Surrey/London/Kelowna); a geometric catch-basin tessellation where it doesn't (Ottawa/Kitchener). |
| **Subcatchment partitioning** | 🟠 Approximated | Which area drains to which inlet is a **nearest-inlet service area, not a surveyed (DEM-derived) watershed.** This is the model's main approximation. |
| **Pipe diameters (synthesized networks)** | 🟠 First-pass design | Rational method per pipe — Q = C·i·A over accumulated upstream subcatchments (C from imperviousness: 0.9 impervious / 0.2 pervious), design intensity from the **nearest ECCC IDF station** at the pipe's time of concentration (10 min inlet floor + travel at 1 m/s), **T = 5 yr**; Manning full-flow diameter rounded UP a commercial ladder (300 mm–3.0 m), no downstream shrinkage. IDF unreachable → documented 30 mm/h constant (noted in provenance). **A plausibility estimate, not a certified sizing.** Real-network cities keep their published diameters. |
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

## Physical imperviousness (ADR 0023 cut 1, #138)

Where OSM maps buildings inside a synthesis cell, `pct_imperv` is the physical estimate
instead of the 30 m land-cover mean:

- **Road half-width 4.0 m** — the paved band each side of a street centreline (~8 m local
  carriageway, curb to curb). One documented number for all street classes.
- **Driveway/sidewalk allowance +10 %** — paved surfaces that ride along mapped roofs but
  are not mapped themselves (driveways, walks, patios).
- **Evidence threshold: roof fraction ≥ 2 %** — cells without mapped buildings keep the
  land-cover value; OSM's suburban sparsity must degrade to the raster, never to zero.
- **Cap 90 %** — even a fully built cell keeps some pervious cracks/verges.
