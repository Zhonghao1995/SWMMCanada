# Moncton fixtures

Recorded **2026-07-28** from the city AGOL org (`services1.arcgis.com/E26PuSoie2Y7bbyI`),
`f=geojson`, WGS84, pagination OK. The `Sewer_Agol3` service is public/token-free but NOT
listed in the open.moncton.ca hub catalogue (licence unstamped — provenance in DATA.md).

## Sub-bbox (EPSG:4326): `-64.815, 46.085, -64.795, 46.100` — central Moncton (~1.7 km)

| file | layer | where | n |
|---|---|---|---|
| `storm_mains.geojson` | `Sewer_Agol3/4` Sewer Main | `UNITTYPE='STM'` | 330 |
| `combined_mains.geojson` | same | `UNITTYPE='COMB'` | 448 |
| `manholes.geojson` | `3` Manholes | — | 810 |
| `inlets.geojson` | `1` Storm Inlet | — | 1025 |
| `sanitary_mains.geojson` | same mains layer | `UNITTYPE='SANI'` | 175 |
| `parcels.geojson` | `Parcels/0` | — | 1507 |
| `buildings.geojson` | `Buildings/0` | — | 1345 |

Field notes: `UPSELEV`/`DWNELEV` inverts (STM 208/330, COMB 369/448; 0 = missing),
`MAINKEY1/2` integer ids joining Manholes `COMPKEY` (MH-prefixed as labels), `PIPESHP` +
`HEIGHT` sections, `PIPETYPE` codes (CONC…). Manholes: `ZTOPCOV` rim (651/810). The
combined core joins the storm graph (ADR 0021); downtown is 448 COMB vs 330 STM.
