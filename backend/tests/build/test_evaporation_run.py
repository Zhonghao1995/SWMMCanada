"""Run the EPA SWMM 5.2 engine on a model that carries an evaporation forcing and assert
it executes cleanly (issue #7, acceptance: 0 errors + continuity within tolerance, with
evaporation actually applied).

Guarded by `swmm5` being on PATH, so CI (which does not install the engine) skips it while
a local/dev box with EPA SWMM installed exercises the real run.
"""
import re
import shutil
import subprocess
from datetime import date, datetime

import pytest

from swmmcanada.build import (
    BuildConfig,
    ConduitIn,
    EvaporationSeries,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    SubcatchmentIn,
    build_model,
)

pytestmark = pytest.mark.skipif(shutil.which("swmm5") is None, reason="EPA SWMM (swmm5) not on PATH")


def _model_with_evaporation(out_dir):
    network = NetworkIn(
        junctions=[JunctionIn("J1", 99.0, 0.0, 0.0), JunctionIn("J2", 98.5, 100.0, 0.0)],
        outfalls=[OutfallIn("O1", 98.0, 200.0, 0.0)],
        conduits=[ConduitIn("C1", "J1", "J2", 100.0), ConduitIn("C2", "J2", "O1", 100.0)],
    )
    subs = [SubcatchmentIn("S1", "J1", area_ha=1.0, pct_imperv=40.0, width_m=100.0, pct_slope=1.0)]
    rain = RainfallSeries(
        timestamps=[datetime(2020, 6, 1, h) for h in range(6)],
        precip_mm=[1.2, 3.4, 0.0, 5.0, 2.0, 0.0],
    )
    evap = EvaporationSeries(
        timestamps=[datetime(2020, 6, 1), datetime(2020, 6, 2)], evap_mm_day=[3.7, 3.9]
    )
    config = BuildConfig(out_dir=out_dir, start=date(2020, 6, 1), end=date(2020, 6, 3))
    return build_model(network=network, subcatchments=subs, rain=rain, config=config, evaporation=evap)


def _continuity_errors(report_text):
    return [float(v) for v in re.findall(r"Continuity Error \(%\)\s*\.+\s*(-?\d+\.?\d*)", report_text)]


def test_model_with_evaporation_runs_clean_in_swmm(tmp_path):
    res = _model_with_evaporation(tmp_path)
    rpt = tmp_path / "model.rpt"
    out = tmp_path / "model.out"

    proc = subprocess.run(["swmm5", str(res.inp_path), str(rpt), str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = rpt.read_text()

    # 0 errors: SWMM writes fatal problems as "ERROR nnn:" lines.
    assert not re.search(r"(?m)^\s*ERROR\b", report), report

    # Evaporation was actually applied (non-zero loss), proving [EVAPORATION] is live.
    evap_loss = re.search(r"Evaporation Loss\s*\.+\s*[\d.]+\s+([\d.]+)", report)
    assert evap_loss and float(evap_loss.group(1)) > 0.0, report

    # Continuity stays within tolerance (both runoff-quantity and flow-routing balances).
    errors = _continuity_errors(report)
    assert errors, report
    assert all(abs(e) < 5.0 for e in errors), errors
