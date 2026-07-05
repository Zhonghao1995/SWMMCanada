"""ADR 0013 — infiltration superset: USDA texture classifier + parameter tables, the
method-matched [INFILTRATION] writer (regression for the switch-mismatch bug), datastore
round-trip of the six new columns, and an opt-in engine smoke per method."""
import re
import shutil
import subprocess
from datetime import date, datetime

import pytest

from swmmcanada.build import (
    BuildConfig, ConduitIn, JunctionIn, NetworkIn, OutfallIn, RainfallSeries,
    SubcatchmentIn, build_model,
)
from swmmcanada.build.config import InfiltrationModel
from swmmcanada.datastore import read_datastore, write_datastore
from swmmcanada.derive import infiltration as infil


# --- USDA texture triangle -----------------------------------------------------------

@pytest.mark.parametrize("clay,sand,expected", [
    (3, 92, "sand"),
    (5, 82, "loamy sand"),
    (10, 65, "sandy loam"),
    (20, 40, "loam"),
    (15, 20, "silt loam"),
    (5, 8, "silt"),
    (25, 60, "sandy clay loam"),
    (33, 33, "clay loam"),
    (33, 10, "silty clay loam"),
    (37, 52, "sandy clay"),
    (45, 7, "silty clay"),
    (60, 20, "clay"),
])
def test_usda_texture_classes(clay, sand, expected):
    assert infil.usda_texture_class(clay, sand) == expected


def test_texture_degenerate_falls_back_to_loam():
    assert infil.usda_texture_class(-5, 200) == "loam"


# --- parameter tables ----------------------------------------------------------------

def test_horton_table_by_hsg():
    assert infil.horton_for_hsg("A") == (127.0, 9.5, 4.14)
    assert infil.horton_for_hsg("D") == (50.8, 0.6, 4.14)
    assert infil.horton_for_hsg(None) == infil.HSG_HORTON["B"]   # unknown -> loamy B row


def test_green_ampt_tiers():
    # Texture tier (real texture) and HSG-representative tier (no-texture fallback)
    assert infil.green_ampt_for_texture("clay") == (316.3, 0.3, 0.385)
    assert infil.green_ampt_for_hsg("D") == infil.GA_BY_TEXTURE["clay"]
    assert infil.green_ampt_for_hsg("B") == infil.GA_BY_TEXTURE["loam"]
    assert infil.green_ampt_for_texture(None) == infil.GA_BY_TEXTURE["loam"]


# --- writer: [OPTIONS] INFILTRATION must match the parameter-row shape ------------------

def _tiny_model(tmp_path, method: InfiltrationModel):
    network = NetworkIn(
        junctions=[JunctionIn("J1", 99.0, 0.0, 0.0), JunctionIn("J2", 98.5, 100.0, 0.0)],
        outfalls=[OutfallIn("O1", 98.0, 200.0, 0.0)],
        conduits=[ConduitIn("C1", "J1", "J2", 100.0), ConduitIn("C2", "J2", "O1", 100.0)],
    )
    subs = [SubcatchmentIn("S1", "J1", area_ha=1.0, pct_imperv=40.0, width_m=100.0,
                           pct_slope=1.0, cn=85.0, horton_f0_mm_h=127.0, horton_fc_mm_h=9.5,
                           horton_decay_1_h=4.14, ga_psi_mm=110.1, ga_ksat_mm_h=10.9,
                           ga_imd=0.412)]
    rain = RainfallSeries(
        timestamps=[datetime(2020, 6, 1, h) for h in range(6)],
        precip_mm=[1.2, 3.4, 0.0, 5.0, 2.0, 0.0],
    )
    config = BuildConfig(out_dir=tmp_path / method.value.lower(),
                         start=date(2020, 6, 1), end=date(2020, 6, 2),
                         infiltration=method)
    return build_model(network=network, subcatchments=subs, rain=rain, config=config)


@pytest.mark.parametrize("method,expected_row", [
    (InfiltrationModel.HORTON, ("127", "9.5", "4.14")),
    (InfiltrationModel.GREEN_AMPT, ("110.1", "10.9", "0.412")),
    (InfiltrationModel.CURVE_NUMBER, ("85",)),
])
def test_infiltration_rows_match_the_options_switch(tmp_path, method, expected_row):
    """The regression for the pre-ADR-0013 bug: OPTIONS said one method while the
    parameter rows were always CN — the engine would mis-read the columns."""
    res = _tiny_model(tmp_path, method)
    text = res.inp_path.read_text()
    m = re.search(r"(?m)^INFILTRATION\s+(\S+)", text)
    assert m and m.group(1) == method.value
    infil_section = text.split("[INFILTRATION]")[1].split("[")[0]
    row = next(l for l in infil_section.splitlines() if l.strip().startswith("S1"))
    for value in expected_row:
        assert value in row, (row, expected_row)


def test_default_infiltration_is_horton(tmp_path):
    """ADR 0013: the build default is Horton (municipal practice)."""
    cfg = BuildConfig(out_dir=tmp_path, start=date(2020, 6, 1), end=date(2020, 6, 1))
    assert cfg.infiltration is InfiltrationModel.HORTON


# --- datastore round-trip of the superset columns --------------------------------------

def test_datastore_roundtrips_all_three_parameter_sets(tmp_path):
    network = NetworkIn(
        junctions=[JunctionIn("J1", 99.0, 0.0, 0.0)],
        outfalls=[OutfallIn("O1", 98.0, 200.0, 0.0)],
        conduits=[ConduitIn("C1", "J1", "O1", 200.0)],
    )
    subs = [SubcatchmentIn("S1", "J1", area_ha=1.0, pct_imperv=40.0, width_m=100.0,
                           pct_slope=1.0, cn=90.0,
                           polygon=[(0.0, 0.0), (0.001, 0.0), (0.001, 0.001), (0.0, 0.0)],
                           horton_f0_mm_h=76.2, horton_fc_mm_h=2.5, horton_decay_1_h=4.14,
                           ga_psi_mm=208.8, ga_ksat_mm_h=1.0, ga_imd=0.390)]
    rain = RainfallSeries(timestamps=[datetime(2020, 6, 1, 0)], precip_mm=[2.0])
    cfg = BuildConfig(out_dir=tmp_path, start=date(2020, 6, 1), end=date(2020, 6, 1),
                      infiltration=InfiltrationModel.GREEN_AMPT)

    write_datastore(tmp_path / "ds", network=network, subcatchments=subs, rain=rain, config=cfg)
    ds = read_datastore(tmp_path / "ds")

    s = ds.subcatchments[0]
    assert (s.horton_f0_mm_h, s.horton_fc_mm_h, s.horton_decay_1_h) == (76.2, 2.5, 4.14)
    assert (s.ga_psi_mm, s.ga_ksat_mm_h, s.ga_imd) == (208.8, 1.0, 0.390)
    assert s.cn == 90.0
    assert ds.config["infiltration"] == "GREEN_AMPT"   # the chosen method is remembered


# --- opt-in engine smoke: each method runs clean in real EPA SWMM ----------------------

@pytest.mark.skipif(shutil.which("swmm5") is None, reason="EPA SWMM (swmm5) not on PATH")
@pytest.mark.parametrize("method", list(InfiltrationModel))
def test_each_method_runs_clean_in_swmm5(tmp_path, method):
    res = _tiny_model(tmp_path, method)
    rpt = res.inp_path.with_suffix(".rpt")
    proc = subprocess.run(
        ["swmm5", str(res.inp_path), str(rpt), str(res.inp_path.with_suffix(".out"))],
        capture_output=True, text=True)
    report = rpt.read_text()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not re.search(r"(?m)^\s*ERROR\b", report), report
