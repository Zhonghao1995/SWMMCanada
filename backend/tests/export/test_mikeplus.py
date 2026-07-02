"""Tests for the MIKE+ CS exporter (ADR 0008): the datastore → a MIKE+ import package.

Verifies the package structure CI must guarantee without a MIKE+ license — files present,
shapefile schemas + `.prj`, all-Polygon catchments in the datastore's projected CRS,
DBF names ≤10 chars, the reciprocal Manning conversion, and that the lossy mapping is
reported (CN→Horton approximated, pct_zero dropped) rather than silently dropped.
"""
from datetime import datetime

import geopandas as gpd
import pytest

from swmmcanada.build import (
    ConduitIn,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    SubcatchmentIn,
)
from swmmcanada.datastore import ModelReadyDatastore
from swmmcanada.export.base import ModelExporter
from swmmcanada.export.mikeplus import MikePlusExporter

# A square polygon near the equator so the projected side stays close to sqrt(area).
_POLY = [(0.0, 0.0), (0.001, 0.0), (0.001, 0.001), (0.0, 0.001)]


def _datastore() -> ModelReadyDatastore:
    network = NetworkIn(
        junctions=[
            JunctionIn(name="J1", invert_m=10.0, x=0.0, y=0.0, max_depth_m=2.0),
            JunctionIn(name="J2", invert_m=9.5, x=0.001, y=0.0, max_depth_m=1.5),
        ],
        outfalls=[OutfallIn(name="O1", invert_m=9.0, x=0.002, y=0.0)],
        conduits=[
            ConduitIn(name="C1", from_node="J1", to_node="J2", length_m=100.0,
                      diameter_m=0.30, roughness_n=0.013),
            ConduitIn(name="C2", from_node="J2", to_node="O1", length_m=120.0,
                      diameter_m=0.40, roughness_n=0.015),
        ],
    )
    subcatchments = [
        SubcatchmentIn(name="S1", outlet_node="J1", area_ha=1.0, pct_imperv=40.0,
                       width_m=50.0, pct_slope=1.5, cn=80.0, n_imperv=0.01, n_perv=0.10,
                       polygon=_POLY),
        SubcatchmentIn(name="S2", outlet_node="J2", area_ha=2.0, pct_imperv=25.0,
                       width_m=80.0, pct_slope=2.0, cn=70.0, polygon=None),
    ]
    rain = RainfallSeries(
        timestamps=[datetime(2020, 6, 1, 0), datetime(2020, 6, 1, 1),
                    datetime(2020, 6, 1, 2)],
        precip_mm=[0.0, 5.0, 2.5],
    )
    return ModelReadyDatastore(
        network=network,
        subcatchments=subcatchments,
        rain=rain,
        config={"start": "2020-06-01", "end": "2020-06-02",
                "coordinate_crs": "EPSG:32610"},
        provenance={},
        evaporation=None,
    )


def test_export_writes_package(tmp_path):
    result = MikePlusExporter().export(_datastore(), tmp_path)

    for name in ("nodes.shp", "links.shp", "catchments.shp", "rain.csv",
                 "field_mapping.md", "README.md"):
        assert (tmp_path / name).exists(), f"missing {name}"
    # geopandas writes a .prj per shapefile — the CRS must survive the import.
    for name in ("nodes.prj", "links.prj", "catchments.prj"):
        assert (tmp_path / name).exists(), f"missing {name}"

    assert result.target == "mikeplus"
    assert result.out_dir == tmp_path


def test_catchments_layer(tmp_path):
    MikePlusExporter().export(_datastore(), tmp_path)
    gdf = gpd.read_file(tmp_path / "catchments.shp")

    # Single geometry type = Polygon (placeholder keeps the None-polygon row a Polygon too).
    assert set(gdf.geometry.geom_type) == {"Polygon"}
    assert gdf.crs.to_epsg() == 32610
    assert all(len(col) <= 10 for col in gdf.columns if col != "geometry")

    s1 = gdf[gdf["MUID"] == "S1"].iloc[0]
    assert s1["ManMImp"] == pytest.approx(1.0 / 0.01)          # M = 1/n
    assert s1["Length"] == pytest.approx((1.0 * 10000.0) / 50.0)  # area_m2 / width


def test_rain_csv_rows(tmp_path):
    ds = _datastore()
    MikePlusExporter().export(ds, tmp_path)
    lines = (tmp_path / "rain.csv").read_text().splitlines()
    assert lines[0] == "datetime,rainfall_mm"
    assert len(lines) - 1 == len(ds.rain.timestamps)  # header excluded


def test_lossy_reported(tmp_path):
    result = MikePlusExporter().export(_datastore(), tmp_path)

    cn = [m for m in result.lossy if m.source == "cn"]
    assert cn and cn[0].kind == "approximated"

    zero = [m for m in result.lossy if m.source == "pct_zero"]
    assert zero and zero[0].kind == "dropped"


def test_satisfies_exporter_protocol():
    assert isinstance(MikePlusExporter(), ModelExporter)


# --- dirty-data defence: one bad record warns, it never kills the export --------


def _dirty_datastore() -> ModelReadyDatastore:
    """Real city data contains zeros: width_m=0, n=0, roughness_n=0 must not crash."""
    ds = _datastore()
    ds.network.conduits.append(
        ConduitIn(name="C_BAD", from_node="J1", to_node="O1", length_m=50.0,
                  diameter_m=0.30, roughness_n=0.0))
    ds.subcatchments.append(
        SubcatchmentIn(name="S_BAD", outlet_node="J2", area_ha=1.0, pct_imperv=30.0,
                       width_m=0.0, pct_slope=1.0, n_imperv=0.0, n_perv=0.0, polygon=None))
    return ds


def test_dirty_data_survives_with_warnings(tmp_path):
    result = MikePlusExporter().export(_dirty_datastore(), tmp_path)

    # The export completed — every file still written.
    for name in ("nodes.shp", "links.shp", "catchments.shp", "rain.csv"):
        assert (tmp_path / name).exists()

    # Each bad value produced a named warning instead of a crash.
    text = "\n".join(result.warnings)
    assert "link C_BAD" in text                      # roughness_n = 0
    assert "catchment S_BAD" in text                 # width_m = 0
    assert "(imperv)" in text and "(perv)" in text   # n_imperv = n_perv = 0


def test_dirty_data_fallback_values(tmp_path):
    MikePlusExporter().export(_dirty_datastore(), tmp_path)

    links = gpd.read_file(tmp_path / "links.shp")
    bad_link = links[links["MUID"] == "C_BAD"].iloc[0]
    assert bad_link["ManningM"] == pytest.approx(75.0)           # stated default M

    cats = gpd.read_file(tmp_path / "catchments.shp")
    bad = cats[cats["MUID"] == "S_BAD"].iloc[0]
    assert bad["Length"] == pytest.approx((1.0 * 10000.0) ** 0.5)  # sqrt(area) fallback
    assert bad["ManMImp"] == pytest.approx(100.0)                # 1/0.01 SWMM default
    assert bad["ManMPrv"] == pytest.approx(10.0)                 # 1/0.10 SWMM default

    # Clean rows are untouched.
    s1 = cats[cats["MUID"] == "S1"].iloc[0]
    assert s1["ManMImp"] == pytest.approx(100.0) and s1["Length"] == pytest.approx(200.0)


def test_clean_data_produces_no_warnings(tmp_path):
    assert MikePlusExporter().export(_datastore(), tmp_path).warnings == []
