"""Tests for the HEC-RAS exporter (ADR 0033): the datastore + the package's 2D raw
materials → a RAS Mapper import package.

What CI must guarantee without HEC-RAS (a Windows-only, GUI-authored geometry): the
package structure; that the pipe network travels as a **system-filtered `.inp`** through the
one `.inp` writer (storm + combined by default, sanitary out, `storm_major` never); that
`projection.prj` is the datastore's projected CRS (the SWMM importer's one hard
requirement); that the lookup tables are verbatim transcriptions of the `derive` dicts
(TR-55 CN by class × HSG, Green-Ampt by HSG tier, NALCMS n / %imperv); the rim-vs-DEM
diagnostic; the outfall → External-node boundary suggestions; the daily-rain warning
(ADR 0033 Q6); and that the lossy report names every field this model class cannot carry.
The first RAS Mapper import (HEC-RAS 7.0.x) is the HITL step.
"""
import csv
from datetime import datetime, timedelta

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from pyproj import CRS
from rasterio.transform import from_origin
from swmm_api import read_inp_file

from swmmcanada.acquire.landcover import DEFAULT_NALCMS_IMPERVIOUS, DEFAULT_NALCMS_LEGEND
from swmmcanada.build import (
    ConduitIn,
    EvaporationSeries,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    SurfaceCatchment,
)
from swmmcanada.build.models import TideSeries
from swmmcanada.datastore import ModelReadyDatastore
from swmmcanada.derive.core import NALCMS_CATEGORY, TR55_CN_TABLE
from swmmcanada.derive.infiltration import GA_BY_TEXTURE, HSG_HORTON, green_ampt_for_hsg
from swmmcanada.export.base import ModelExporter
from swmmcanada.export.hecras import (
    DEFAULT_NALCMS_MANNING_N,
    DEFAULT_SYSTEMS,
    HecRasExporter,
    export_hecras,
    precipitation_interval,
)

_T0 = datetime(2020, 6, 1)
_POLY = [(-123.360, 48.420), (-123.359, 48.420), (-123.359, 48.421), (-123.360, 48.421)]
_CRS = "EPSG:32610"


def _rain(step_h: float = 1.0, n: int = 6) -> RainfallSeries:
    return RainfallSeries(timestamps=[_T0 + timedelta(hours=step_h * i) for i in range(n)],
                          precip_mm=[0.0, 5.0, 2.5, 1.0, 0.0, 0.0][:n])


def _datastore(*, rain=None, tide=True, provenance=None, crs=_CRS) -> ModelReadyDatastore:
    """Storm + combined wired together, sanitary separate, one tidal and one free outfall."""
    network = NetworkIn(
        junctions=[
            JunctionIn("J1", invert_m=10.0, x=-123.3600, y=48.4200, max_depth_m=2.0),
            JunctionIn("J2", invert_m=9.5, x=-123.3590, y=48.4200, max_depth_m=1.5),
            JunctionIn("K1", invert_m=9.8, x=-123.3595, y=48.4205, max_depth_m=1.8,
                       system="combined"),
            JunctionIn("SAN_J1", invert_m=8.0, x=-123.3580, y=48.4210, system="sanitary"),
        ],
        outfalls=[
            OutfallIn("O1", invert_m=9.0, x=-123.3580, y=48.4200),                     # FREE
            OutfallIn("O2", invert_m=8.5, x=-123.3585, y=48.4195, kind="TIDAL",
                      synthesised=True),
            OutfallIn("SAN_O1", invert_m=7.0, x=-123.3570, y=48.4210, system="sanitary"),
        ],
        conduits=[
            ConduitIn("C1", "J1", "J2", length_m=100.0, diameter_m=0.30, roughness_n=0.013),
            ConduitIn("C2", "J2", "O1", length_m=120.0, diameter_m=0.40, roughness_n=0.015),
            ConduitIn("K1C", "K1", "J2", length_m=60.0, diameter_m=0.45, system="combined"),
            ConduitIn("C3", "J2", "O2", length_m=100.0, diameter_m=0.40,
                      shape="RECT_CLOSED", height_m=0.6, width_m=0.9),
            ConduitIn("SAN_C1", "SAN_J1", "SAN_O1", length_m=50.0, diameter_m=0.20,
                      system="sanitary"),
        ],
    )
    subcatchments = [
        SurfaceCatchment("S1", outlet_node="J1", area_ha=1.0, pct_imperv=40.0, width_m=50.0,
                         pct_slope=1.5, cn=80.0, polygon=_POLY),
        SurfaceCatchment("S2", outlet_node="J2", area_ha=2.0, pct_imperv=25.0, width_m=80.0,
                         pct_slope=2.0, cn=70.0),
        SurfaceCatchment("SK", outlet_node="K1", area_ha=0.7, pct_imperv=60.0, width_m=40.0,
                         pct_slope=1.0, cn=85.0, system="combined"),
        SurfaceCatchment("S_SAN", outlet_node="SAN_J1", area_ha=0.5, pct_imperv=30.0,
                         width_m=30.0, pct_slope=1.0, system="sanitary"),
    ]
    tide_series = TideSeries(timestamps=[_T0 + timedelta(hours=i) for i in range(6)],
                             level_m=[0.1, 0.4, 0.8, 0.9, 0.5, 0.0]) if tide else None
    return ModelReadyDatastore(
        network=network, subcatchments=subcatchments, rain=rain or _rain(),
        config={"start": "2020-06-01", "end": "2020-06-02", "coordinate_crs": crs},
        provenance=provenance or {"aoi_bbox": [-123.361, 48.419, -123.356, 48.4215]},
        evaporation=EvaporationSeries(timestamps=[_T0], evap_mm_day=[3.0]),
        tide=tide_series,
    )


def _write_dem(path, value: float = 11.0, *, hole_at=None):
    """A tiny lon/lat GeoTIFF over the fixture nodes; ``hole_at`` punches nodata at (lon,lat)."""
    res = 0.0005
    west, north = -123.362, 48.423
    data = np.full((12, 16), value, dtype="float32")
    transform = from_origin(west, north, res, res)
    if hole_at is not None:
        col = int((hole_at[0] - west) / res)
        row = int((north - hole_at[1]) / res)
        data[row, col] = -9999.0
    with rasterio.open(path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1],
                       count=1, dtype="float32", crs="EPSG:4326", transform=transform,
                       nodata=-9999.0) as dst:
        dst.write(data, 1)
    return path


def _rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# structure, protocol, the .inp carrier
# --------------------------------------------------------------------------- #
def test_conforms_to_the_export_protocol():
    assert isinstance(HecRasExporter(), ModelExporter)
    assert HecRasExporter().target == "hecras"


def test_package_structure(tmp_path):
    res = HecRasExporter().export(_datastore(), tmp_path / "hecras")
    names = {p.name for p in res.files}
    assert names >= {"model.inp", "model_build_manifest.json", "projection.prj",
                     "aoi_2d_area.shp", "landcover_table.csv", "infiltration_scs_cn.csv",
                     "infiltration_green_ampt.csv", "rain.csv", "tide.csv",
                     "nodes_supplement.csv", "boundary_conditions.csv", "field_mapping.md",
                     "README.md"}
    assert all(p.exists() for p in res.files)
    # No second `manifest.json` inside the package (the root one is the package manifest).
    assert not (tmp_path / "hecras" / "manifest.json").exists()


def test_the_inp_is_the_pipe_network_carrier_filtered_to_surface_water_systems(tmp_path):
    """RAS Mapper's SWMM importer reads the .inp; sanitary manholes would import as
    surface-connected nodes they physically are not — so they are out by default and the
    combined mains (which carry a combined city's stormwater) are in."""
    res = HecRasExporter().export(_datastore(), tmp_path / "hecras")
    assert set(res.view["systems"]) == set(DEFAULT_SYSTEMS) == {"storm_minor", "combined"}
    inp = read_inp_file(str(tmp_path / "hecras" / "model.inp"))
    junctions = set(inp["JUNCTIONS"].keys())
    assert junctions == {"J1", "J2", "K1"}
    assert set(inp["OUTFALLS"].keys()) == {"O1", "O2"}
    assert set(inp["CONDUITS"].keys()) == {"C1", "C2", "K1C", "C3"}
    assert set(inp["SUBCATCHMENTS"].keys()) == {"S1", "S2", "SK"}   # S_SAN left with its node


def test_an_explicit_selection_is_honoured_but_storm_major_never_enters(tmp_path):
    res = HecRasExporter().export(_datastore(), tmp_path / "hecras",
                                  systems=["storm_minor", "sanitary", "storm_major"])
    assert set(res.view["systems"]) == {"storm_minor", "sanitary"}
    inp = read_inp_file(str(tmp_path / "hecras" / "model.inp"))
    assert "SAN_J1" in inp["JUNCTIONS"] and "K1" not in inp["JUNCTIONS"]


def test_projection_prj_is_the_datastore_crs(tmp_path):
    res = HecRasExporter().export(_datastore(), tmp_path / "hecras")
    wkt = (tmp_path / "hecras" / "projection.prj").read_text()
    assert wkt.startswith("PROJCS[")                       # ESRI WKT1, what RAS Mapper reads
    assert CRS.from_wkt(wkt).equals(CRS.from_user_input(_CRS), ignore_axis_order=True)
    # and the 2D area shapefile is written in that same CRS
    gdf = gpd.read_file(tmp_path / "hecras" / "aoi_2d_area.shp")
    assert CRS.from_user_input(gdf.crs).equals(CRS.from_user_input(_CRS), ignore_axis_order=True)
    assert len(gdf) == 1 and gdf.geometry.iloc[0].area > 0
    assert not res.warnings or all("projection.prj" not in w for w in res.warnings)


def test_no_projected_crs_means_no_prj_and_a_warning(tmp_path):
    res = HecRasExporter().export(_datastore(crs=None), tmp_path / "hecras")
    assert not (tmp_path / "hecras" / "projection.prj").exists()
    assert any("projection.prj not written" in w for w in res.warnings)


def test_aoi_polygon_from_provenance_geojson_beats_the_bbox(tmp_path):
    prov = {"aoi_bbox": [-123.361, 48.419, -123.356, 48.4215],
            "aoi_geojson": {"type": "Polygon", "coordinates": [[
                (-123.361, 48.419), (-123.356, 48.419), (-123.3585, 48.4215), (-123.361, 48.419)]]}}
    HecRasExporter().export(_datastore(provenance=prov), tmp_path / "hecras")
    gdf = gpd.read_file(tmp_path / "hecras" / "aoi_2d_area.shp").to_crs("EPSG:4326")
    assert len(gdf.geometry.iloc[0].exterior.coords) == 4     # the triangle, not the box


# --------------------------------------------------------------------------- #
# lookup tables == the derive dicts (transcription fidelity)
# --------------------------------------------------------------------------- #
def test_landcover_table_transcribes_the_code_dicts(tmp_path):
    HecRasExporter().export(_datastore(), tmp_path / "hecras")
    rows = _rows(tmp_path / "hecras" / "landcover_table.csv")
    assert {int(r["landcover_code"]) for r in rows} == set(DEFAULT_NALCMS_LEGEND)
    for r in rows:
        code = int(r["landcover_code"])
        assert r["landcover_name"] == DEFAULT_NALCMS_LEGEND[code]
        assert float(r["manning_n"]) == DEFAULT_NALCMS_MANNING_N[code]
        assert float(r["impervious_pct"]) == pytest.approx(100.0 * DEFAULT_NALCMS_IMPERVIOUS[code])
        assert r["tr55_category"] == NALCMS_CATEGORY[code]


def test_scs_cn_table_is_the_tr55_pervious_table_per_class_and_hsg(tmp_path):
    HecRasExporter().export(_datastore(), tmp_path / "hecras")
    rows = _rows(tmp_path / "hecras" / "infiltration_scs_cn.csv")
    assert len(rows) == len(DEFAULT_NALCMS_LEGEND) * 4
    for r in rows:
        cat = NALCMS_CATEGORY[int(r["landcover_code"])]
        assert r["tr55_category"] == cat
        assert float(r["curve_number"]) == TR55_CN_TABLE[cat][r["hsg"]]
    # built-up rows carry the PERVIOUS-remainder CN (round-2 F-021), never a composite one
    built = [r for r in rows if r["tr55_category"] == "built"]
    assert {float(r["curve_number"]) for r in built} == set(TR55_CN_TABLE["built"].values())


def test_green_ampt_table_hsg_tier_and_texture_rows_only_when_the_raster_ships(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    HecRasExporter().export(_datastore(), root / "hecras")
    rows = _rows(root / "hecras" / "infiltration_green_ampt.csv")
    assert [r["layer"] for r in rows] == ["hsg"] * 4
    for r in rows:
        psi, ksat, imd = green_ampt_for_hsg(r["class"])
        assert (float(r["psi_mm"]), float(r["ksat_mm_h"]), float(r["imd"])) == (psi, ksat, imd)
        f0, fc, k = HSG_HORTON[r["class"]]
        assert (float(r["horton_f0_mm_h"]), float(r["horton_fc_mm_h"])) == (f0, fc)
        assert "not_representable" in r["note"]

    (root / "soil_texture.tif").write_bytes(b"")     # presence is what the writer keys on
    HecRasExporter().export(_datastore(), root / "hecras")
    rows = _rows(root / "hecras" / "infiltration_green_ampt.csv")
    texture = [r for r in rows if r["layer"] == "texture"]
    assert len(texture) == len(GA_BY_TEXTURE)
    assert {r["class"] for r in texture} == set(GA_BY_TEXTURE)


# --------------------------------------------------------------------------- #
# forcing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("step_h,token", [(1.0, "1HOUR"), (24.0, "1DAY"), (0.25, "15MIN"),
                                          (6.0, "6HOUR")])
def test_precipitation_interval_token(step_h, token):
    assert precipitation_interval(_rain(step_h).timestamps) == token


def test_daily_rain_warns_loudly_hourly_does_not(tmp_path):
    hourly = HecRasExporter().export(_datastore(), tmp_path / "h")
    assert not any("DAILY" in w for w in hourly.warnings)
    daily = HecRasExporter().export(_datastore(rain=_rain(24.0)), tmp_path / "d")
    assert any("DAILY" in w and "design-storm" in w for w in daily.warnings)
    # provenance saying "daily" is enough on its own
    prov = {"aoi_bbox": [-123.361, 48.419, -123.356, 48.4215],
            "forcing": {"rainfall_resolution": "daily"}}
    by_prov = HecRasExporter().export(_datastore(provenance=prov), tmp_path / "p")
    assert any("rainfall_resolution=daily" in w for w in by_prov.warnings)


def test_rain_and_tide_csvs(tmp_path):
    HecRasExporter().export(_datastore(), tmp_path / "hecras")
    rain = _rows(tmp_path / "hecras" / "rain.csv")
    assert [r["rainfall_mm"] for r in rain][:3] == ["0.0", "5.0", "2.5"]
    tide = _rows(tmp_path / "hecras" / "tide.csv")
    assert len(tide) == 6 and float(tide[3]["stage_m"]) == 0.9


# --------------------------------------------------------------------------- #
# nodes supplement + boundary conditions
# --------------------------------------------------------------------------- #
def test_nodes_supplement_flags_surface_connected_nodes_and_defaults(tmp_path):
    HecRasExporter().export(_datastore(), tmp_path / "hecras")
    by = {r["node"]: r for r in _rows(tmp_path / "hecras" / "nodes_supplement.csv")}
    assert set(by) == {"J1", "J2", "K1", "O1", "O2"}          # the view, no sanitary
    assert by["J1"]["rim_m"] == "12.0" and by["J1"]["drop_inlet"] == "1"
    assert by["J1"]["drop_inlet_elev_m"] == "12.0"
    assert float(by["J1"]["drop_inlet_weir_length_m"]) == 0.9
    assert float(by["J1"]["base_area_m2"]) == 1.13
    assert by["J2"]["n_subcatchments"] == "1" and by["J2"]["drain_area_ha"] == "2.0"
    assert by["O1"]["node_kind"] == "outfall" and by["O1"]["drop_inlet"] == "0"
    assert by["O2"]["synthesised"] == "1"
    assert by["J1"]["dem_m"] == ""                             # no DEM beside the package


def test_rim_vs_dem_diagnostic_and_the_error_node_warning(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    _write_dem(root / "dem_dtm.tif", value=11.0)              # J1 rim 12.0, J2 rim 11.0
    res = HecRasExporter().export(_datastore(), root / "hecras")
    by = {r["node"]: r for r in _rows(root / "hecras" / "nodes_supplement.csv")}
    assert float(by["J1"]["dem_m"]) == 11.0
    assert float(by["J1"]["rim_minus_dem_m"]) == pytest.approx(1.0)
    assert by["J1"]["invert_ge_dem"] == "0"
    assert not any("Error nodes" in w for w in res.warnings)

    _write_dem(root / "dem_dtm.tif", value=9.6)               # J1 invert 10.0 >= 9.6
    res = HecRasExporter().export(_datastore(), root / "hecras")
    by = {r["node"]: r for r in _rows(root / "hecras" / "nodes_supplement.csv")}
    assert by["J1"]["invert_ge_dem"] == "1" and by["J2"]["invert_ge_dem"] == "0"
    assert any("Error nodes" in w and "J1" in w for w in res.warnings)
    assert not any("dem_dtm.tif not found" in w for w in res.warnings)


def test_boundary_conditions_per_outfall(tmp_path):
    HecRasExporter().export(_datastore(), tmp_path / "hecras")
    by = {r["outfall"]: r for r in _rows(tmp_path / "hecras" / "boundary_conditions.csv")}
    assert set(by) == {"O1", "O2"}
    assert by["O1"]["hecras_bc"] == "Normal Depth"
    assert float(by["O1"]["normal_depth_slope"]) == pytest.approx((9.5 - 9.0) / 120.0, abs=1e-5)
    assert by["O2"]["hecras_bc"] == "Stage Hydrograph" and by["O2"]["stage_source"] == "tide.csv"
    assert "synthesised" in by["O2"]["note"]


def test_tidal_outfall_without_a_tide_series_falls_back_to_normal_depth(tmp_path):
    HecRasExporter().export(_datastore(tide=False), tmp_path / "hecras")
    by = {r["outfall"]: r for r in _rows(tmp_path / "hecras" / "boundary_conditions.csv")}
    assert by["O2"]["hecras_bc"] == "Normal Depth"
    assert "no tide series" in by["O2"]["note"]
    assert not (tmp_path / "hecras" / "tide.csv").exists()


# --------------------------------------------------------------------------- #
# lossy report + docs
# --------------------------------------------------------------------------- #
def test_lossy_report_names_what_this_model_class_cannot_carry(tmp_path):
    res = HecRasExporter().export(_datastore(), tmp_path / "hecras")
    by_kind = {}
    for m in res.lossy:
        by_kind.setdefault(m.kind, []).append(m.source)
    dropped = " ".join(by_kind["dropped"])
    for f in ("width_m", "pct_slope", "n_imperv", "s_perv_mm", "pct_zero"):
        assert f in dropped                                        # routing params
    assert any("evaporation" in s for s in by_kind["dropped"])
    assert any(s.startswith("horton") for s in by_kind["approximated"])   # no Horton in RAS
    assert any(s.startswith("pct_imperv") for s in by_kind["approximated"])
    assert any(s.startswith("cn") for s in by_kind["restructured"])
    assert any(s.startswith("polygon") for s in by_kind["restructured"])
    assert any("tide" in s for s in by_kind["restructured"])          # not dropped, unlike ICM
    assert any("non-circular" in s for s in by_kind["approximated"])  # C3 is RECT_CLOSED
    assert any("soil HSG raster" in s for s in by_kind["dropped"])     # no ../hsg.tif here
    text = (tmp_path / "hecras" / "field_mapping.md").read_text()
    for m in res.lossy:
        assert m.source.split(" ")[0] in text
    assert "storm_minor, combined" in text and "storm_major" in text


def test_readme_gives_the_ras_mapper_steps_and_the_interval(tmp_path):
    HecRasExporter().export(_datastore(), tmp_path / "hecras")
    readme = (tmp_path / "hecras" / "README.md").read_text()
    for step in ("Import SWMM Geometry", "projection.prj", "Create a New RAS Terrain",
                 "Land Cover", "Infiltration Layer", "Interval=1HOUR", "ras_flow_set_hydrograph"):
        assert step in readme
    assert "unchecked" in readme      # subcatchments-as-2D-areas left off by default


def test_export_hecras_reads_a_datastore_directory(tmp_path):
    """The pipeline entry point: datastore dir → package; 2D materials found beside it."""
    from swmmcanada.build.config import BuildConfig
    from swmmcanada.datastore import write_datastore

    ds = _datastore()
    root = tmp_path / "pkg"
    ds_dir = root / "datastore"
    write_datastore(ds_dir, network=ds.network, subcatchments=ds.subcatchments, rain=ds.rain,
                    config=BuildConfig(out_dir=root, start=_T0.date(), end=_T0.date(),
                                       coordinate_crs=_CRS),
                    provenance=ds.provenance, evaporation=ds.evaporation, tide=ds.tide)
    _write_dem(root / "dem_dtm.tif")
    res = export_hecras(ds_dir, root / "hecras")
    assert res.target == "hecras"
    assert (root / "hecras" / "model.inp").exists()
    assert not any("dem_dtm.tif not found" in w for w in res.warnings)
    by = {r["node"]: r for r in _rows(root / "hecras" / "nodes_supplement.csv")}
    assert by["J1"]["dem_m"] != ""
