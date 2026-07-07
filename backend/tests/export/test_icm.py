"""Tests for the InfoWorks ICM exporter (ADR 0012): the datastore → an ODIC import package.

Verifies what CI must guarantee without an ICM license — package structure, the Auto-Map
column names on nodes/conduits CSVs, the unit conversions (conduit width **mm** ×1000 above
all, areas ha, slope m/m, Manning n as-is), the lossless CN pass-through (no Horton
approximation — the headline difference from MIKE+), storm-only filtering (ADR 0011), and
that the lossy report is complete. The first import into a licensed ICM is the manual
verification step (ADR 0012 §5), tracked as a HITL follow-up.
"""
import csv
from datetime import datetime

import geopandas as gpd

from swmmcanada.build import (
    ConduitIn,
    EvaporationSeries,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    SubcatchmentIn,
)
from swmmcanada.datastore import ModelReadyDatastore
from swmmcanada.export.base import ModelExporter
from swmmcanada.export.icm import IcmExporter

# A square polygon near the equator so the projected side stays close to sqrt(area).
_POLY = [(0.0, 0.0), (0.001, 0.0), (0.001, 0.001), (0.0, 0.001)]


def _datastore() -> ModelReadyDatastore:
    network = NetworkIn(
        junctions=[
            JunctionIn(name="J1", invert_m=10.0, x=0.0, y=0.0, max_depth_m=2.0),
            JunctionIn(name="J2", invert_m=9.5, x=0.001, y=0.0, max_depth_m=1.5),
            JunctionIn(name="SAN_J1", invert_m=8.0, x=0.002, y=0.001, system="sanitary"),
        ],
        outfalls=[
            OutfallIn(name="O1", invert_m=9.0, x=0.002, y=0.0),
            OutfallIn(name="SAN_O1", invert_m=7.0, x=0.003, y=0.001, system="sanitary"),
        ],
        conduits=[
            ConduitIn(name="C1", from_node="J1", to_node="J2", length_m=100.0,
                      diameter_m=0.30, roughness_n=0.013),
            ConduitIn(name="C1b", from_node="J1", to_node="J2", length_m=101.0,
                      diameter_m=0.25, roughness_n=0.013),   # parallel pipe, same node pair
            ConduitIn(name="C2", from_node="J2", to_node="O1", length_m=120.0,
                      diameter_m=0.40, roughness_n=0.015),
            ConduitIn(name="SAN_C1", from_node="SAN_J1", to_node="SAN_O1", length_m=50.0,
                      diameter_m=0.20, roughness_n=0.013, system="sanitary"),
        ],
    )
    subcatchments = [
        SubcatchmentIn(name="S1", outlet_node="J1", area_ha=1.0, pct_imperv=40.0,
                       width_m=50.0, pct_slope=1.5, cn=80.0, n_imperv=0.01, n_perv=0.10,
                       polygon=_POLY),
        SubcatchmentIn(name="S2", outlet_node="J2", area_ha=2.0, pct_imperv=25.0,
                       width_m=80.0, pct_slope=2.0, cn=70.0, polygon=None),
        SubcatchmentIn(name="S_SAN", outlet_node="SAN_J1", area_ha=0.5, pct_imperv=30.0,
                       width_m=30.0, pct_slope=1.0, system="sanitary"),
    ]
    rain = RainfallSeries(
        timestamps=[datetime(2020, 6, 1, 0), datetime(2020, 6, 1, 1),
                    datetime(2020, 6, 1, 2)],
        precip_mm=[0.0, 5.0, 2.5],
    )
    evap = EvaporationSeries(
        timestamps=[datetime(2020, 6, 1)], evap_mm_day=[3.0])
    return ModelReadyDatastore(
        network=network,
        subcatchments=subcatchments,
        rain=rain,
        config={"start": "2020-06-01", "end": "2020-06-02",
                "coordinate_crs": "EPSG:32610"},
        provenance={},
        evaporation=evap,
    )


def _rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_icm_exporter_conforms_to_interface():
    exp = IcmExporter()
    assert isinstance(exp, ModelExporter)
    assert exp.target == "icm"


def test_package_files_all_present(tmp_path):
    res = IcmExporter().export(_datastore(), tmp_path)
    names = {p.name for p in res.files}
    assert names == {"nodes.csv", "conduits.csv", "subcatchments.shp",
                     "rain_infoworks.csv", "rain.csv", "field_mapping.md", "README.md"}
    assert all(p.exists() for p in res.files)
    assert res.target == "icm"
    assert (tmp_path / "subcatchments.prj").exists()   # CRS sidecar for the shapefile


def test_nodes_csv_automap_columns_and_levels(tmp_path):
    IcmExporter().export(_datastore(), tmp_path)
    rows = _rows(tmp_path / "nodes.csv")
    assert list(rows[0]) == ["node_id", "x", "y", "node_type", "system_type",
                             "ground_level", "chamber_floor"]     # InfoWorks DB field names
    by_id = {r["node_id"]: r for r in rows}
    assert set(by_id) == {"J1", "J2", "O1"}                       # storm only — no SAN_*
    assert float(by_id["J1"]["ground_level"]) == 12.0             # invert + max_depth
    assert float(by_id["J1"]["chamber_floor"]) == 10.0
    assert by_id["J1"]["node_type"] == "Manhole"
    assert by_id["O1"]["node_type"] == "Outfall"
    # x,y are written in the display CRS (UTM 10N) — not raw lon/lat
    assert abs(float(by_id["J1"]["x"])) > 1000


def test_conduits_csv_units_and_suffixes(tmp_path):
    IcmExporter().export(_datastore(), tmp_path)
    rows = _rows(tmp_path / "conduits.csv")
    assert [r["us_node_id"] for r in rows] == ["J1", "J1", "J2"]  # storm only
    c1, c1b, c2 = rows
    assert float(c1["conduit_width"]) == 300.0                    # 0.30 m → mm (the ×1000 trap)
    assert float(c1["conduit_height"]) == 300.0
    assert float(c2["conduit_width"]) == 400.0
    assert c1["shape"] == "CIRC"
    assert c1["roughness_type"] == "N"
    assert float(c1["bottom_roughness_N"]) == 0.013               # Manning n as-is, no 1/n
    assert float(c1["us_invert"]) == 10.0 and float(c1["ds_invert"]) == 9.5
    assert c1["link_suffix"] != c1b["link_suffix"]                # parallel pipes distinct
    assert float(c1["conduit_length"]) == 100.0


def test_subcatchments_shp_units_cn_and_geometry(tmp_path):
    IcmExporter().export(_datastore(), tmp_path)
    gdf = gpd.read_file(tmp_path / "subcatchments.shp")
    assert gdf.crs.to_epsg() == 32610
    assert set(gdf["sub_id"]) == {"S1", "S2"}                     # sanitary-draining omitted
    assert (gdf.geometry.geom_type == "Polygon").all()            # placeholder square incl.
    s1 = gdf[gdf["sub_id"] == "S1"].iloc[0]
    assert s1["total_area"] == 1.0                                # ha, as-is
    assert s1["slope"] == 0.015                                   # % → m/m
    assert s1["cn"] == 80.0                                       # lossless pass-through
    assert abs(s1["dimension"] - (10000.0 / 3.141592653589793) ** 0.5) < 1e-6
    assert s1["imp_pct"] == 40.0 and s1["prv_pct"] == 60.0
    # ADR 0013: Horton + Green-Ampt parameter sets ride along (DBF-safe names, as-is units)
    assert all(len(c) <= 10 for c in gdf.columns if c != "geometry")
    for col in ("hort_f0", "hort_fc", "hort_decay", "ga_psi_mm", "ga_ksat", "ga_imd"):
        assert col in gdf.columns, col
    assert s1["hort_f0"] == 101.6 and s1["hort_fc"] == 5.7        # model defaults (fixture)
    assert s1["ga_psi_mm"] == 88.9 and s1["ga_imd"] == 0.434      # loam row defaults


def test_rain_event_csv_infoworks_grammar(tmp_path):
    IcmExporter().export(_datastore(), tmp_path)
    text = (tmp_path / "rain_infoworks.csv").read_text().splitlines()
    assert text[0] == "!Version=2,type=RED,charset=UTF8"
    assert "G_START,G_TS,G_NPROFILES,G_ARD,G_EVAP" in text
    assert "01-06-2020 00:00,60m,1,0,0" in text                   # hourly series → 60m step
    assert text[-3:] == ["01-06-2020 00:00,0.0", "01-06-2020 01:00,5.0",
                         "01-06-2020 02:00,2.5"]
    # plain fallback ships alongside
    plain = _rows(tmp_path / "rain.csv")
    assert len(plain) == 3 and plain[1]["rainfall_mm"] == "5.0"


def test_export_icm_from_datastore_dir(tmp_path):
    """The pipeline path (ADR 0012 §5): datastore directory → ``export_icm`` → package.
    Proves the system tags survive the disk round-trip into the storm-only filter."""
    from datetime import date

    from swmmcanada.build import BuildConfig
    from swmmcanada.datastore import write_datastore
    from swmmcanada.export.icm import export_icm

    ds = _datastore()
    cfg = BuildConfig(out_dir=tmp_path / "x", start=date(2020, 6, 1), end=date(2020, 6, 2),
                      coordinate_crs="EPSG:32610")
    write_datastore(tmp_path / "ds", network=ds.network, subcatchments=ds.subcatchments,
                    rain=ds.rain, config=cfg, evaporation=ds.evaporation)
    res = export_icm(tmp_path / "ds", tmp_path / "icm")
    assert res.target == "icm" and (tmp_path / "icm" / "nodes.csv").exists()
    rows = _rows(tmp_path / "icm" / "conduits.csv")
    assert len(rows) == 3                                        # sanitary conduit filtered
    assert all(r["system_type"] == "storm" for r in rows)


def test_lossy_report_no_cn_loss_but_drops_reported(tmp_path):
    res = IcmExporter().export(_datastore(), tmp_path)
    by_source = {m.source: m for m in res.lossy}
    assert "cn" not in by_source                    # THE headline: CN is lossless in ICM
    assert by_source["pct_zero"].kind == "dropped"
    assert by_source["evaporation series"].kind == "dropped"     # evap present in fixture
    assert by_source["width_m"].kind == "restructured"
    assert by_source["polygon"].kind == "approximated"           # S2 placeholder square
    # sanitary-draining subcatchment omission is surfaced, not silent
    assert any("S_SAN" in w for w in res.warnings)
    # and the sheet carries the report + the Auto-Map contract
    sheet = (tmp_path / "field_mapping.md").read_text()
    assert "curve_number" in sheet and "Auto-Map" in sheet and "pct_zero" in sheet


def test_readme_points_2d_modellers_at_the_raw_materials(tmp_path):
    """ADR 0009 amendment: the package IS the 2D raw-material delivery — the README must
    say where terrain/roughness live and that meshing stays in the engineer's tool."""
    IcmExporter().export(_datastore(), tmp_path)
    text = (tmp_path / "README.md").read_text()
    assert "For 2D overland modelling" in text
    assert "dem_dtm.tif" in text and "landcover.tif" in text
