"""Run EPA SWMM 5.2 on a model whose subcatchments come from the v2 DEM delineator
(ADR 0010 acceptance d): the model executes cleanly with sane continuity — a runnability
smoke, deliberately NOT an accuracy claim (calibration is aiswmm's domain).

Guarded by `swmm5` on PATH (CI skips; a dev box with EPA SWMM runs it)."""
import re
import shutil
import subprocess
from datetime import date, datetime

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.crs import CRS

from swmmcanada.build import (
    BuildConfig,
    ConduitIn,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    build_model,
)
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.delineate_dem import DemDelineationConfig, delineate_junction_subcatchments

pytestmark = pytest.mark.skipif(shutil.which("swmm5") is None, reason="EPA SWMM (swmm5) not on PATH")

DEM_CRS = "EPSG:32618"
RES, N = 10.0, 100
X0, Y0 = 500_000.0, 5_000_000.0
_TO_LL = Transformer.from_crs(DEM_CRS, "EPSG:4326", always_xy=True).transform


def _valley_dem(tmp_path):
    rows = np.abs(np.arange(N) - 50)[:, None] * 1.0
    tilt = (N - np.arange(N))[None, :] * 0.05
    arr = (rows + tilt + 100.0).astype("float32")
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=N, width=N, count=1, dtype="float32",
        crs=CRS.from_string(DEM_CRS), transform=Affine(RES, 0, X0, 0, -RES, Y0), nodata=-9999.0,
    ) as dst:
        dst.write(arr, 1)
    return path


def _lonlat(col, row):
    return _TO_LL(X0 + col * RES, Y0 - row * RES)


def test_v2_delineated_model_runs_clean_in_swmm(tmp_path):
    aoi = aoi_from_geojson({"type": "Polygon", "coordinates": [[
        list(_lonlat(5, 95)), list(_lonlat(95, 95)), list(_lonlat(95, 5)),
        list(_lonlat(5, 5)), list(_lonlat(5, 95))]]})
    jxy = {"JW": _lonlat(30, 50), "JE": _lonlat(70, 50)}

    subs, diag = delineate_junction_subcatchments(
        jxy, aoi, dem_path=_valley_dem(tmp_path), config=DemDelineationConfig(slope_gate_pct=3.0))
    assert diag["method"] == "junction_dem"      # the DEM path is what we're smoking

    network = NetworkIn(
        junctions=[JunctionIn("JW", 99.0, *jxy["JW"]), JunctionIn("JE", 98.5, *jxy["JE"])],
        outfalls=[OutfallIn("O1", 98.0, *_lonlat(90, 50))],
        conduits=[ConduitIn("C1", "JW", "JE", 400.0), ConduitIn("C2", "JE", "O1", 200.0)],
    )
    rain = RainfallSeries(
        timestamps=[datetime(2020, 6, 1, h) for h in range(6)],
        precip_mm=[1.2, 3.4, 0.0, 5.0, 2.0, 0.0],
    )
    res = build_model(network=network, subcatchments=subs, rain=rain,
                      config=BuildConfig(out_dir=tmp_path, start=date(2020, 6, 1), end=date(2020, 6, 3)))

    rpt, out = tmp_path / "model.rpt", tmp_path / "model.out"
    proc = subprocess.run(["swmm5", str(res.inp_path), str(rpt), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = rpt.read_text()
    assert not re.search(r"(?m)^\s*ERROR\b", report), report
    errors = [float(v) for v in re.findall(r"Continuity Error \(%\)\s*\.+\s*(-?\d+\.?\d*)", report)]
    assert errors and all(abs(e) < 5.0 for e in errors), errors
