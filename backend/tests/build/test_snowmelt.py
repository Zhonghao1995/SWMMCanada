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
