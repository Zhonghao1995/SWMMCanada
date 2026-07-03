"""Snowmelt (#55): a temperature series turns on [TEMPERATURE] + [SNOWPACKS], every
subcatchment references the URBAN pack, and the datastore round-trips it (ADR 0007)."""
from datetime import date, datetime

from swmm_api import read_inp_file
from swmm_api.input_file import SEC

from swmmcanada.build import (
    BuildConfig,
    ConduitIn,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    SubcatchmentIn,
    TemperatureSeries,
    build_model,
)


def _net():
    return NetworkIn(
        junctions=[JunctionIn("J1", 99.0, -75.70, 45.41), JunctionIn("J2", 98.5, -75.69, 45.41)],
        outfalls=[OutfallIn("O1", 98.0, -75.68, 45.41)],
        conduits=[ConduitIn("C1", "J1", "J2", 100.0), ConduitIn("C2", "J2", "O1", 100.0)],
    )


def _subs():
    return [SubcatchmentIn("S1", "J1", 1.0, 40.0, 100.0, 1.0),
            SubcatchmentIn("S2", "J2", 0.5, 30.0, 70.0, 1.0)]


def _rain():
    return RainfallSeries([datetime(2020, 1, 1, h) for h in range(3)], [1.0, 2.0, 0.0])


def _temp():
    return TemperatureSeries([datetime(2020, 1, 1), datetime(2020, 1, 2)], [-5.0, -3.0])


def _cfg(out):
    return BuildConfig(out_dir=out, start=date(2020, 1, 1), end=date(2020, 1, 3))


def test_temperature_turns_on_snowmelt(tmp_path):
    res = build_model(network=_net(), subcatchments=_subs(), rain=_rain(),
                      config=_cfg(tmp_path), temperature=_temp())
    assert "TEMPERATURE" in res.sections_written and "SNOWPACKS" in res.sections_written

    inp = read_inp_file(str(res.inp_path))
    for s in inp[SEC.SUBCATCHMENTS].values():
        assert str(s.snow_pack) == "URBAN"           # every subcatchment references the pack
    assert "temperature" in inp[SEC.TIMESERIES]      # the series rides along
    text = res.inp_path.read_text()
    assert "SNOWMELT" in text                        # dividing temp / ATI / lat climatology line


def test_no_temperature_no_snow_sections(tmp_path):
    res = build_model(network=_net(), subcatchments=_subs(), rain=_rain(), config=_cfg(tmp_path))
    assert "TEMPERATURE" not in res.sections_written
    assert "SNOWPACKS" not in res.sections_written


def test_snowmelt_survives_the_datastore_path(tmp_path):
    """ADR 0007: build consumes temperature now, so the datastore must reconstruct it and
    the datastore-built .inp must carry the same snow sections as the direct build."""
    from swmmcanada.datastore import build_from_datastore, read_datastore, write_datastore

    write_datastore(tmp_path / "ds", network=_net(), subcatchments=_subs(), rain=_rain(),
                    config=_cfg(tmp_path / "x"), temperature=_temp())
    ds = read_datastore(tmp_path / "ds")
    assert ds.temperature is not None
    assert ds.temperature.tmean_c == [-5.0, -3.0]

    res = build_from_datastore(tmp_path / "ds", tmp_path / "b")
    assert "TEMPERATURE" in res.sections_written and "SNOWPACKS" in res.sections_written


# --- the real engine, cold season (guarded like test_evaporation_run) --------------
import re
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("swmm5") is None, reason="EPA SWMM (swmm5) not on PATH")
def test_winter_model_runs_clean_and_snow_engages(tmp_path):
    """Sub-zero rain must land as snowpack, then melt on the warm day — EPA SWMM runs
    clean and its runoff continuity block accounts for snow (proving [SNOWPACKS] is live,
    not just parseable)."""
    temp = TemperatureSeries(
        [datetime(2020, 1, 1), datetime(2020, 1, 2), datetime(2020, 1, 3), datetime(2020, 1, 4)],
        [-8.0, -5.0, 6.0, 8.0],                        # two freezing days, then a thaw
    )
    rain = RainfallSeries([datetime(2020, 1, 1, h) for h in range(6)],
                          [2.0, 3.0, 2.5, 1.0, 0.5, 0.0])   # falls while T < 0 → snow
    res = build_model(network=_net(), subcatchments=_subs(), rain=rain,
                      config=BuildConfig(out_dir=tmp_path, start=date(2020, 1, 1),
                                         end=date(2020, 1, 4)),
                      temperature=temp)

    rpt, out = tmp_path / "model.rpt", tmp_path / "model.out"
    proc = subprocess.run(["swmm5", str(res.inp_path), str(rpt), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = rpt.read_text()
    assert not re.search(r"(?m)^\s*ERROR\b", report), report
    errors = [float(v) for v in re.findall(r"Continuity Error \(%\)\s*\.+\s*(-?\d+\.?\d*)", report)]
    assert errors and all(abs(e) < 5.0 for e in errors), errors
    assert re.search(r"(?i)snow", report), "runoff continuity never mentions snow"
